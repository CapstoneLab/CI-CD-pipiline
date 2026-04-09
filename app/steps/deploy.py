from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from app.models import StepRunResult
from app.utils.logger import append_log
from app.utils.shell import run_command


# ---------------------------------------------------------------------------
# S3 bucket & EC2 connection info (matches CDK stack outputs)
# ---------------------------------------------------------------------------
S3_BUCKET = "cicd-artifacts-668568918251"
EC2_REGION = "us-east-1"

# Nginx config & deployment root on EC2
EC2_DEPLOY_ROOT = "/opt/deployments"
EC2_NGINX_CONF_DIR = "/opt/deployments/nginx"
EC2_USER = "ec2-user"

# Port range for dynamic app allocation
_PORT_RANGE_START = 3001
_PORT_RANGE_END = 3999


def run_deploy(
    repo_dir: Path,
    run_dir: Path,
    log_file: Path,
    repo_url: str,
    branch: str | None,
) -> StepRunResult:
    """Deploy build artifacts to AWS EC2 via S3."""

    # ------------------------------------------------------------------
    # 1. Extract user/repo from git URL
    # ------------------------------------------------------------------
    owner, repo_name = _parse_github_url(repo_url)
    if not owner or not repo_name:
        msg = f"Cannot parse owner/repo from URL: {repo_url}"
        append_log(log_file, msg)
        return StepRunResult(status="failed", exit_code=1, summary_message=msg)

    deploy_key = f"{owner}/{repo_name}"
    append_log(log_file, f"Deploy target: {deploy_key}")

    # ------------------------------------------------------------------
    # 2. Locate build artifacts
    # ------------------------------------------------------------------
    artifacts_dir = run_dir / "artifacts"
    if not artifacts_dir.exists() or not any(artifacts_dir.iterdir()):
        msg = "No build artifacts found. Build step must run before deploy."
        append_log(log_file, msg)
        return StepRunResult(status="failed", exit_code=1, summary_message=msg)

    append_log(log_file, f"Artifacts directory: {artifacts_dir}")

    # ------------------------------------------------------------------
    # 3. Detect runtime type from repo contents
    # ------------------------------------------------------------------
    runtime = _detect_runtime(repo_dir)
    append_log(log_file, f"Detected runtime: {runtime}")

    # ------------------------------------------------------------------
    # 4. Rewrite asset paths for frontend SPA (before hash & upload)
    # ------------------------------------------------------------------
    if runtime in ("react", "vue", "angular"):
        base_path = f"/{owner}/{repo_name}"
        append_log(log_file, f"Rewriting frontend asset paths with base: {base_path}")
        _rewrite_frontend_paths(artifacts_dir, base_path, log_file)

    # ------------------------------------------------------------------
    # 5. Compute artifact hash for duplicate detection
    # ------------------------------------------------------------------
    artifact_hash = _compute_artifacts_hash(artifacts_dir)
    append_log(log_file, f"Artifact hash: {artifact_hash}")

    # ------------------------------------------------------------------
    # 6. Upload artifacts to S3
    # ------------------------------------------------------------------
    s3_prefix = f"deployments/{owner}/{repo_name}/{artifact_hash}"
    append_log(log_file, f"Uploading to s3://{S3_BUCKET}/{s3_prefix}/")

    result = run_command(
        command=[
            "aws", "s3", "sync",
            str(artifacts_dir),
            f"s3://{S3_BUCKET}/{s3_prefix}/",
            "--region", EC2_REGION,
            "--delete",
        ],
        cwd=run_dir,
        log_file=log_file,
    )
    if result.exit_code != 0:
        return StepRunResult(
            status="failed",
            exit_code=result.exit_code,
            summary_message="Failed to upload artifacts to S3",
        )

    # ------------------------------------------------------------------
    # 6. Upload deploy manifest to S3
    # ------------------------------------------------------------------
    manifest = {
        "owner": owner,
        "repo": repo_name,
        "branch": branch or "main",
        "runtime": runtime,
        "artifact_hash": artifact_hash,
        "s3_prefix": s3_prefix,
        "deploy_path": f"{EC2_DEPLOY_ROOT}/apps/{owner}/{repo_name}",
    }
    manifest_path = run_dir / "deploy_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    result = run_command(
        command=[
            "aws", "s3", "cp",
            str(manifest_path),
            f"s3://{S3_BUCKET}/{s3_prefix}/deploy_manifest.json",
            "--region", EC2_REGION,
        ],
        cwd=run_dir,
        log_file=log_file,
    )
    if result.exit_code != 0:
        return StepRunResult(
            status="failed",
            exit_code=result.exit_code,
            summary_message="Failed to upload deploy manifest to S3",
        )

    # ------------------------------------------------------------------
    # 7. Trigger deployment on EC2 via SSM Run Command
    # ------------------------------------------------------------------
    deploy_script = _build_ec2_deploy_script(
        owner=owner,
        repo_name=repo_name,
        runtime=runtime,
        s3_bucket=S3_BUCKET,
        s3_prefix=s3_prefix,
        artifact_hash=artifact_hash,
    )
    append_log(log_file, "Sending deploy command to EC2 via SSM...")

    # Get EC2 instance ID
    instance_id = _get_deploy_instance_id(log_file)
    if not instance_id:
        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message="Cannot find deploy EC2 instance. Is the CDK stack deployed?",
        )

    append_log(log_file, f"Target EC2 instance: {instance_id}")

    # Send command via SSM
    result = run_command(
        command=[
            "aws", "ssm", "send-command",
            "--instance-ids", instance_id,
            "--document-name", "AWS-RunShellScript",
            "--parameters", json.dumps({"commands": [deploy_script]}),
            "--region", EC2_REGION,
            "--output", "json",
        ],
        cwd=run_dir,
        log_file=log_file,
    )

    if result.exit_code != 0:
        append_log(log_file, "SSM send-command failed, trying to check if SSM agent is ready...")
        return StepRunResult(
            status="failed",
            exit_code=result.exit_code,
            summary_message=f"SSM deploy command failed. Instance {instance_id} may not have SSM agent ready yet.",
        )

    # Extract command ID and wait for completion
    command_id = _extract_command_id(result.output)
    if command_id:
        append_log(log_file, f"SSM Command ID: {command_id}")
        wait_result = run_command(
            command=[
                "aws", "ssm", "wait", "command-executed",
                "--command-id", command_id,
                "--instance-id", instance_id,
                "--region", EC2_REGION,
            ],
            cwd=run_dir,
            log_file=log_file,
        )

        # Fetch command output
        output_result = run_command(
            command=[
                "aws", "ssm", "get-command-invocation",
                "--command-id", command_id,
                "--instance-id", instance_id,
                "--region", EC2_REGION,
                "--output", "json",
            ],
            cwd=run_dir,
            log_file=log_file,
        )

        if output_result.exit_code == 0:
            try:
                invocation = json.loads(output_result.output)
                ssm_status = invocation.get("Status", "Unknown")
                stdout_content = invocation.get("StandardOutputContent", "")
                stderr_content = invocation.get("StandardErrorContent", "")
                if stdout_content:
                    append_log(log_file, f"[EC2 stdout] {stdout_content}")
                if stderr_content:
                    append_log(log_file, f"[EC2 stderr] {stderr_content}")

                if ssm_status != "Success":
                    return StepRunResult(
                        status="failed",
                        exit_code=1,
                        summary_message=f"EC2 deploy script failed with status: {ssm_status}",
                    )
            except (json.JSONDecodeError, KeyError):
                pass

    append_log(log_file, f"Deploy successful: http://<EC2_IP>/{owner}/{repo_name}")

    return StepRunResult(
        status="success",
        exit_code=0,
        summary_message=f"Deployed {deploy_key} ({runtime}) to EC2 | hash={artifact_hash[:12]}",
    )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo_name) from a GitHub URL."""
    patterns = [
        r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?$",
        r"github\.com[:/]([^/]+)/([^/.]+?)/?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    return "", ""


def _detect_runtime(repo_dir: Path) -> str:
    """Detect project runtime: node-backend, react, vue, angular, nextjs, python, or java."""
    pkg_json = repo_dir / "package.json"
    if pkg_json.exists():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            scripts = pkg.get("scripts", {})

            # Next.js detection (must be before react since next projects also have react)
            if "next" in deps:
                return "nextjs"
            # React detection
            if "react-scripts" in deps or "react" in deps:
                # Check if it has a server entry point → backend
                for f in ["server.js", "server.ts", "src/server.js", "src/server.ts"]:
                    if (repo_dir / f).exists():
                        return "node"
                return "react"
            # Vue detection
            if "@vue/cli-service" in deps or "vue" in deps or "vite" in deps:
                # Vite could be react too, but if vue is present it's vue
                if "vue" in deps:
                    return "vue"
                # Vite without vue — check for react
                if "react" in deps:
                    return "react"
                return "vue"
            # Angular detection
            if "@angular/core" in deps or "@angular/cli" in deps:
                return "angular"
            # Has server entry → node backend
            for f in ["server.js", "index.js", "app.js", "main.js", "src/server.js"]:
                if (repo_dir / f).exists():
                    return "node"
            # Has start script but no known frontend framework → node backend
            if "start" in scripts:
                return "node"
        except (json.JSONDecodeError, KeyError):
            return "node"
        return "node"
    if (repo_dir / "requirements.txt").exists() or (repo_dir / "pyproject.toml").exists():
        return "python"
    if (repo_dir / "pom.xml").exists() or (repo_dir / "build.gradle").exists():
        return "java"
    return "node"


def _rewrite_frontend_paths(artifacts_dir: Path, base_path: str, log_file: Path) -> None:
    """Rewrite absolute asset paths in HTML/JS so SPA works under a subpath."""
    for html_file in artifacts_dir.rglob("*.html"):
        content = html_file.read_text(encoding="utf-8", errors="replace")
        original = content
        # Fix src="/static/..." and href="/static/..."
        content = content.replace('="/static/', f'="{base_path}/static/')
        # Fix manifest, favicon, logo references
        content = content.replace('="/manifest', f'="{base_path}/manifest')
        content = content.replace('="/favicon', f'="{base_path}/favicon')
        content = content.replace('="/logo', f'="{base_path}/logo')
        # Fix og:image and other meta content with absolute paths
        content = re.sub(r'content="/((?:static|assets|images|img)/)', rf'content="{base_path}/\1', content)
        if content != original:
            html_file.write_text(content, encoding="utf-8")
            append_log(log_file, f"  Rewrote paths in {html_file.name}")

    # Fix JS files that reference /static/ paths
    for js_file in artifacts_dir.rglob("*.js"):
        try:
            content = js_file.read_text(encoding="utf-8", errors="replace")
            original = content
            content = content.replace('"/static/', f'"{base_path}/static/')
            if content != original:
                js_file.write_text(content, encoding="utf-8")
        except Exception:
            pass


def _compute_artifacts_hash(artifacts_dir: Path) -> str:
    """Compute SHA256 hash of all artifact files for dedup."""
    hasher = hashlib.sha256()
    for file_path in sorted(artifacts_dir.rglob("*")):
        if file_path.is_file():
            hasher.update(str(file_path.relative_to(artifacts_dir)).encode())
            hasher.update(file_path.read_bytes())
    return hasher.hexdigest()


def _get_deploy_instance_id(log_file: Path) -> str:
    """Find the EC2 instance ID from the CDK-deployed stack."""
    result = run_command(
        command=[
            "aws", "ec2", "describe-instances",
            "--filters",
            "Name=tag:aws:cloudformation:stack-name,Values=CiCdDeployStack",
            "Name=instance-state-name,Values=running",
            "--query", "Reservations[0].Instances[0].InstanceId",
            "--output", "text",
            "--region", EC2_REGION,
        ],
        cwd=Path("."),
        log_file=log_file,
    )
    instance_id = result.output.strip().splitlines()[-1].strip() if result.output.strip() else ""
    if instance_id and instance_id != "None" and instance_id.startswith("i-"):
        return instance_id
    return ""


def _extract_command_id(ssm_output: str) -> str:
    """Extract CommandId from SSM send-command JSON output."""
    try:
        data = json.loads(ssm_output)
        return data.get("Command", {}).get("CommandId", "")
    except (json.JSONDecodeError, KeyError):
        return ""


def _build_ec2_deploy_script(
    owner: str,
    repo_name: str,
    runtime: str,
    s3_bucket: str,
    s3_prefix: str,
    artifact_hash: str,
) -> str:
    """Build the shell script that runs on EC2 to deploy the app."""
    app_dir = f"{EC2_DEPLOY_ROOT}/apps/{owner}/{repo_name}"
    hash_file = f"{app_dir}/.deploy_hash"
    port_file = f"{EC2_DEPLOY_ROOT}/.port_registry"
    nginx_conf = f"{EC2_NGINX_CONF_DIR}/{owner}__{repo_name}.conf"

    return f"""#!/bin/bash
set -ex

APP_DIR="{app_dir}"
HASH_FILE="{hash_file}"
ARTIFACT_HASH="{artifact_hash}"
PORT_FILE="{port_file}"
NGINX_CONF="{nginx_conf}"
S3_PATH="s3://{s3_bucket}/{s3_prefix}/"
RUNTIME="{runtime}"
OWNER="{owner}"
REPO="{repo_name}"

# --- Ensure deploy directories exist ---
mkdir -p {EC2_DEPLOY_ROOT}/apps
mkdir -p {EC2_DEPLOY_ROOT}/nginx
mkdir -p {EC2_DEPLOY_ROOT}/www
touch "$PORT_FILE"

# --- Ensure Nginx config exists ---
if [ ! -f /etc/nginx/conf.d/deployments.conf ]; then
    cat > /etc/nginx/conf.d/deployments.conf << 'NGINXCONF'
server {{
    listen 80 default_server;
    server_name _;
    location / {{
        root /opt/deployments/www;
        index index.html;
        try_files $uri $uri/ =404;
    }}
    include /opt/deployments/nginx/*.conf;
}}
NGINXCONF
    rm -f /etc/nginx/conf.d/default.conf
    nginx -t && systemctl reload nginx
fi

# --- Duplicate check ---
if [ -f "$HASH_FILE" ]; then
    CURRENT_HASH=$(cat "$HASH_FILE")
    if [ "$CURRENT_HASH" = "$ARTIFACT_HASH" ]; then
        echo "SKIP: Artifact hash unchanged ($ARTIFACT_HASH). No redeploy needed."
        exit 0
    fi
fi

# --- Allocate port ---
touch "$PORT_FILE"
EXISTING_PORT=$(grep "^$OWNER/$REPO " "$PORT_FILE" 2>/dev/null | awk '{{print $2}}' || true)
if [ -n "$EXISTING_PORT" ]; then
    PORT=$EXISTING_PORT
else
    LAST_PORT=$(awk '{{print $2}}' "$PORT_FILE" 2>/dev/null | sort -n | tail -1 || echo "{_PORT_RANGE_START - 1}")
    if [ -z "$LAST_PORT" ]; then
        PORT={_PORT_RANGE_START}
    else
        PORT=$((LAST_PORT + 1))
    fi
    echo "$OWNER/$REPO $PORT" >> "$PORT_FILE"
fi

echo "Deploying $OWNER/$REPO on port $PORT (runtime: $RUNTIME)"

# --- Stop existing process ---
PM2_HOME=/etc/.pm2 pm2 delete "$OWNER--$REPO" 2>/dev/null || true
pkill -f "gunicorn.*$OWNER--$REPO" 2>/dev/null || true
if [ -f "$APP_DIR/.java.pid" ]; then
    kill $(cat "$APP_DIR/.java.pid") 2>/dev/null || true
    rm -f "$APP_DIR/.java.pid"
fi

# --- Download artifacts from S3 ---
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR"
aws s3 sync "$S3_PATH" "$APP_DIR/" --region {EC2_REGION} --delete

# --- Save hash ---
echo "$ARTIFACT_HASH" > "$HASH_FILE"

# --- Start application ---
cd "$APP_DIR"

# Find the actual app directory (first subdirectory with content)
APP_ROOT="$APP_DIR"
# Frontend frameworks: React→build, Vue/Angular→dist, Next.js→.next+out
# Backend fallback: dist-server
if [ "$RUNTIME" = "react" ]; then
    for d in build dist out; do
        [ -d "$APP_DIR/$d" ] && APP_ROOT="$APP_DIR/$d" && break
    done
elif [ "$RUNTIME" = "vue" ] || [ "$RUNTIME" = "angular" ]; then
    for d in dist build out; do
        [ -d "$APP_DIR/$d" ] && APP_ROOT="$APP_DIR/$d" && break
    done
elif [ "$RUNTIME" = "nextjs" ]; then
    # Next.js static export → out/, or standalone → .next/standalone/
    if [ -d "$APP_DIR/out" ]; then
        APP_ROOT="$APP_DIR/out"
    elif [ -d "$APP_DIR/.next/standalone" ]; then
        APP_ROOT="$APP_DIR/.next/standalone"
    elif [ -d "$APP_DIR/.next" ]; then
        APP_ROOT="$APP_DIR/.next"
    fi
else
    for d in dist dist-server build out; do
        [ -d "$APP_DIR/$d" ] && APP_ROOT="$APP_DIR/$d" && break
    done
fi

echo "APP_ROOT: $APP_ROOT"
SERVE_MODE="proxy"

# ============================================================
# Frontend: React / Vue / Angular (static files via Nginx)
# ============================================================
if [ "$RUNTIME" = "react" ] || [ "$RUNTIME" = "vue" ] || [ "$RUNTIME" = "angular" ]; then
    SERVE_MODE="static"
    echo "Frontend app detected ($RUNTIME). Serving static files from $APP_ROOT"
    echo "Asset paths already rewritten by CI engine before upload."

# ============================================================
# Next.js
# ============================================================
elif [ "$RUNTIME" = "nextjs" ]; then
    # Case 1: Static export (out/ with index.html)
    if [ -f "$APP_ROOT/index.html" ]; then
        SERVE_MODE="static"
        echo "Next.js static export detected. Serving from $APP_ROOT"
    # Case 2: Standalone server
    elif [ -f "$APP_DIR/.next/standalone/server.js" ]; then
        APP_ROOT="$APP_DIR/.next/standalone"
        # Copy static and public assets for standalone
        cp -r "$APP_DIR/.next/static" "$APP_ROOT/.next/static" 2>/dev/null || true
        cp -r "$APP_DIR/public" "$APP_ROOT/public" 2>/dev/null || true
        cd "$APP_ROOT"
        PM2_HOME=/etc/.pm2 PORT=$PORT pm2 start server.js --name "$OWNER--$REPO"
        sleep 3
        ACTUAL_PORT=$(ss -tlnp 2>/dev/null | grep "node" | grep -oP ':\\K[0-9]+(?=\\s)' | sort -n | tail -1 || true)
        if [ -n "$ACTUAL_PORT" ] && [ "$ACTUAL_PORT" != "$PORT" ]; then
            PORT=$ACTUAL_PORT
            sed -i "s|^$OWNER/$REPO .*|$OWNER/$REPO $PORT|" "$PORT_FILE"
        fi
        echo "Next.js standalone server on port $PORT"
    # Case 3: Regular Next.js SSR (has .next but no standalone, no static export)
    elif [ -d "$APP_DIR/.next" ]; then
        cd "$APP_DIR"
        # Install next if not present
        if ! command -v next &>/dev/null && [ ! -f node_modules/.bin/next ]; then
            npm install next react react-dom 2>/dev/null || true
        fi
        PM2_HOME=/etc/.pm2 pm2 start "npx next start -p $PORT" \\
            --name "$OWNER--$REPO" \\
            --interpreter none \\
            --cwd "$APP_DIR"
        sleep 5
        ACTUAL_PORT=$(ss -tlnp 2>/dev/null | grep "$PORT" | grep -oP ':\\K[0-9]+(?=\\s)' | head -1 || true)
        if [ -n "$ACTUAL_PORT" ]; then
            PORT=$ACTUAL_PORT
        fi
        echo "Next.js SSR server on port $PORT"
    else
        SERVE_MODE="static"
        echo "Next.js fallback to static serving"
    fi

# ============================================================
# Node.js backend
# ============================================================
elif [ "$RUNTIME" = "node" ]; then
    if [ -f "$APP_ROOT/package.json" ]; then
        cd "$APP_ROOT"
        npm install --omit=dev 2>/dev/null || true
    fi
    ENTRY=""
    for f in server.js index.js app.js main.js; do
        [ -f "$APP_ROOT/$f" ] && ENTRY="$APP_ROOT/$f" && break
    done
    if [ -z "$ENTRY" ] && [ -f "$APP_ROOT/package.json" ]; then
        ENTRY=$(node -e "try{{console.log(require('./package.json').main||'')}}catch(e){{}}" 2>/dev/null || true)
        [ -n "$ENTRY" ] && ENTRY="$APP_ROOT/$ENTRY"
    fi
    if [ -n "$ENTRY" ] && [ -f "$ENTRY" ]; then
        PM2_HOME=/etc/.pm2 PORT=$PORT pm2 start "$ENTRY" --name "$OWNER--$REPO"
        sleep 3
        ACTUAL_PORT=$(ss -tlnp 2>/dev/null | grep "node" | grep -oP ':\\K[0-9]+(?=\\s)' | sort -n | tail -1 || true)
        if [ -n "$ACTUAL_PORT" ] && [ "$ACTUAL_PORT" != "$PORT" ]; then
            PORT=$ACTUAL_PORT
            sed -i "s|^$OWNER/$REPO .*|$OWNER/$REPO $PORT|" "$PORT_FILE"
        fi
    else
        SERVE_MODE="static"
        echo "No Node.js entry point found. Serving as static site."
    fi

# ============================================================
# Python backend
# ============================================================
elif [ "$RUNTIME" = "python" ]; then
    cd "$APP_ROOT"
    pip3 install -r requirements.txt 2>/dev/null || true
    WSGI_APP=""
    for f in app.py main.py wsgi.py application.py; do
        if [ -f "$APP_ROOT/$f" ]; then
            WSGI_APP="$(basename "$f" .py):app"
            break
        fi
    done
    if [ -n "$WSGI_APP" ]; then
        gunicorn "$WSGI_APP" -b "0.0.0.0:$PORT" -D --pid "$APP_DIR/.gunicorn.pid" \\
            --access-logfile "$APP_DIR/access.log" --error-logfile "$APP_DIR/error.log" \\
            --name "$OWNER--$REPO"
    fi

# ============================================================
# Java backend
# ============================================================
elif [ "$RUNTIME" = "java" ]; then
    JAR=$(find "$APP_ROOT" -name "*.jar" -type f | head -1)
    if [ -n "$JAR" ]; then
        nohup java -jar "$JAR" --server.port=$PORT > "$APP_DIR/java.log" 2>&1 &
        echo $! > "$APP_DIR/.java.pid"
    fi
fi

# --- Configure Nginx ---
if [ "$SERVE_MODE" = "static" ]; then
    # SPA-friendly static serving with proper alias + try_files
    cat > "$NGINX_CONF" << NGINXEOF
location /$OWNER/$REPO {{
    return 301 /$OWNER/$REPO/;
}}
location /$OWNER/$REPO/ {{
    alias $APP_ROOT/;
    index index.html;
    try_files \\$uri \\$uri/ /$OWNER/$REPO/index.html;
}}
NGINXEOF
else
    cat > "$NGINX_CONF" << NGINXEOF
location /$OWNER/$REPO {{
    return 301 /$OWNER/$REPO/;
}}
location /$OWNER/$REPO/ {{
    proxy_pass http://127.0.0.1:$PORT/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade \\$http_upgrade;
    proxy_set_header Connection 'upgrade';
    proxy_set_header Host \\$host;
    proxy_set_header X-Real-IP \\$remote_addr;
    proxy_set_header X-Forwarded-For \\$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \\$scheme;
    proxy_cache_bypass \\$http_upgrade;
}}
NGINXEOF
fi

# --- Reload Nginx ---
nginx -t && systemctl reload nginx

# --- Generate index page with deployed app list ---
INDEX_FILE="{EC2_DEPLOY_ROOT}/www/index.html"
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null || true)
HOSTNAME=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || echo "unknown")

cat > "$INDEX_FILE" << 'HEADEREOF'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CI/CD Deploy Server</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
  .container {{ max-width: 900px; margin: 0 auto; padding: 40px 20px; }}
  h1 {{ font-size: 2rem; font-weight: 700; margin-bottom: 8px; color: #f8fafc; }}
  .subtitle {{ color: #94a3b8; margin-bottom: 32px; font-size: 0.95rem; }}
  .stats {{ display: flex; gap: 16px; margin-bottom: 32px; }}
  .stat-card {{ background: #1e293b; border-radius: 12px; padding: 16px 24px; flex: 1; border: 1px solid #334155; }}
  .stat-value {{ font-size: 1.5rem; font-weight: 700; color: #38bdf8; }}
  .stat-label {{ font-size: 0.8rem; color: #94a3b8; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 12px; overflow: hidden; border: 1px solid #334155; }}
  th {{ background: #334155; padding: 14px 20px; text-align: left; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; font-weight: 600; }}
  td {{ padding: 14px 20px; border-top: 1px solid #334155; }}
  tr:hover td {{ background: #263348; }}
  a {{ color: #38bdf8; text-decoration: none; font-weight: 500; }}
  a:hover {{ text-decoration: underline; color: #7dd3fc; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }}
  .badge-node {{ background: #064e3b; color: #6ee7b7; }}
  .badge-react {{ background: #164e63; color: #67e8f9; }}
  .badge-vue {{ background: #14532d; color: #86efac; }}
  .badge-angular {{ background: #7f1d1d; color: #fca5a5; }}
  .badge-next {{ background: #1c1917; color: #e7e5e4; border: 1px solid #44403c; }}
  .badge-python {{ background: #1e3a5f; color: #93c5fd; }}
  .badge-java {{ background: #7c2d12; color: #fdba74; }}
  .badge-static {{ background: #3f3f46; color: #d4d4d8; }}
  .port {{ font-family: 'SF Mono', 'Fira Code', monospace; color: #a78bfa; font-size: 0.9rem; }}
  .empty {{ text-align: center; padding: 60px 20px; color: #64748b; }}
  .footer {{ margin-top: 32px; text-align: center; color: #475569; font-size: 0.8rem; }}
</style>
</head>
<body>
<div class="container">
<h1>CI/CD Deploy Server</h1>
<p class="subtitle">Deployed applications via Local CI Engine</p>
HEADEREOF

# Count apps and build table rows
APP_COUNT=0
TABLE_ROWS=""

while IFS=' ' read -r APP_PATH APP_PORT; do
    [ -z "$APP_PATH" ] && continue
    APP_COUNT=$((APP_COUNT + 1))

    APP_OWNER=$(echo "$APP_PATH" | cut -d'/' -f1)
    APP_REPO=$(echo "$APP_PATH" | cut -d'/' -f2-)
    APP_DIR_CHECK="{EC2_DEPLOY_ROOT}/apps/$APP_PATH"

    # Detect runtime from deploy_manifest.json
    MANIFEST_RUNTIME=$(cat "$APP_DIR_CHECK/deploy_manifest.json" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('runtime','node'))" 2>/dev/null || echo "node")
    BADGE_CLASS="badge-node"
    BADGE_TEXT="Node.js"
    case "$MANIFEST_RUNTIME" in
        react)    BADGE_CLASS="badge-react";   BADGE_TEXT="React" ;;
        vue)      BADGE_CLASS="badge-vue";     BADGE_TEXT="Vue" ;;
        angular)  BADGE_CLASS="badge-angular"; BADGE_TEXT="Angular" ;;
        nextjs)   BADGE_CLASS="badge-next";    BADGE_TEXT="Next.js" ;;
        python)   BADGE_CLASS="badge-python";  BADGE_TEXT="Python" ;;
        java)     BADGE_CLASS="badge-java";    BADGE_TEXT="Java" ;;
        node)     BADGE_CLASS="badge-node";    BADGE_TEXT="Node.js" ;;
        *)        BADGE_CLASS="badge-static";  BADGE_TEXT="Static" ;;
    esac

    # Check if process is actually running
    STATUS_DOT="\\xF0\\x9F\\x9F\\xA2"  # green circle
    if ! ss -tlnp 2>/dev/null | grep -q ":$APP_PORT "; then
        STATUS_DOT="\\xF0\\x9F\\x94\\xB4"  # red circle
    fi

    TABLE_ROWS="$TABLE_ROWS<tr><td>$(echo -e $STATUS_DOT) <a href=\\\"/$APP_PATH/\\\">$APP_OWNER/$APP_REPO</a></td><td><span class=\\\"badge $BADGE_CLASS\\\">$BADGE_TEXT</span></td><td class=\\\"port\\\">:$APP_PORT</td><td><a href=\\\"/$APP_PATH/\\\">/$APP_PATH/</a></td></tr>"
done < "$PORT_FILE"

# Write stats
cat >> "$INDEX_FILE" << STATSEOF
<div class="stats">
  <div class="stat-card">
    <div class="stat-value">$APP_COUNT</div>
    <div class="stat-label">Deployed Apps</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">$HOSTNAME</div>
    <div class="stat-label">Server IP</div>
  </div>
</div>
STATSEOF

if [ "$APP_COUNT" -gt 0 ]; then
    cat >> "$INDEX_FILE" << TABLEEOF
<table>
  <thead><tr><th>Application</th><th>Runtime</th><th>Port</th><th>URL</th></tr></thead>
  <tbody>$TABLE_ROWS</tbody>
</table>
TABLEEOF
else
    cat >> "$INDEX_FILE" << EMPTYEOF
<div class="empty"><p>No applications deployed yet.</p><p>Run the CI/CD pipeline to deploy your first app.</p></div>
EMPTYEOF
fi

cat >> "$INDEX_FILE" << 'FOOTEREOF'
<div class="footer">Powered by Local CI Engine</div>
</div>
</body>
</html>
FOOTEREOF

echo "Deploy complete: /$OWNER/$REPO -> port $PORT (runtime: $RUNTIME)"
"""
