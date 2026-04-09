from aws_cdk import (
    Stack,
    RemovalPolicy,
    CfnOutput,
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
)
from constructs import Construct


class InfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ──────────────────────────────────────────────
        # 1. VPC (public subnet only - free tier friendly)
        # ──────────────────────────────────────────────
        vpc = ec2.Vpc(self, "CiCdVpc",
            max_azs=2,
            nat_gateways=0,  # NAT Gateway is not free
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                # Private subnet reserved for future RDS
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
        )

        # ──────────────────────────────────────────────
        # 2. Security Group
        # ──────────────────────────────────────────────
        sg = ec2.SecurityGroup(self, "DeploySg",
            vpc=vpc,
            description="Security group for CI/CD deploy EC2",
            allow_all_outbound=True,
        )
        # SSH
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "SSH")
        # HTTP (Nginx reverse proxy)
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(80), "HTTP")
        # HTTPS (future)
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(443), "HTTPS")

        # ──────────────────────────────────────────────
        # 3. S3 Bucket for build artifacts
        # ──────────────────────────────────────────────
        artifact_bucket = s3.Bucket(self, "ArtifactBucket",
            bucket_name=f"cicd-artifacts-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # ──────────────────────────────────────────────
        # 4. IAM Role for EC2 (S3 read access)
        # ──────────────────────────────────────────────
        role = iam.Role(self, "DeployEc2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                # SSM agent needs this to receive commands
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )
        artifact_bucket.grant_read(role)

        # ──────────────────────────────────────────────
        # 5. EC2 Key Pair
        # ──────────────────────────────────────────────
        key_pair = ec2.KeyPair(self, "DeployKeyPair",
            key_pair_name="capstone-deploy-key",
        )

        # ──────────────────────────────────────────────
        # 6. User Data - install runtimes & Nginx
        # ──────────────────────────────────────────────
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "#!/bin/bash",
            "set -ex",

            # System update
            "yum update -y",

            # Install Nginx
            "amazon-linux-extras install nginx1 -y 2>/dev/null || yum install nginx -y",
            "systemctl enable nginx",
            "systemctl start nginx",

            # Install Node.js 20 (LTS)
            "curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -",
            "yum install -y nodejs",
            "npm install -g pm2",

            # Install Python 3.11 + pip
            "yum install -y python3.11 python3.11-pip 2>/dev/null || yum install -y python3 python3-pip",
            "pip3 install gunicorn",

            # Install Java 17 (Corretto - Amazon's OpenJDK)
            "yum install -y java-17-amazon-corretto-headless",

            # Install AWS CLI (for S3 artifact pull)
            "yum install -y aws-cli",

            # Create deploy directories
            "mkdir -p /opt/deployments",
            "chown ec2-user:ec2-user /opt/deployments",

            # Nginx default config for reverse proxy
            """cat > /etc/nginx/conf.d/deployments.conf << 'NGINXCONF'
# Dynamic reverse proxy for deployed apps
# Pattern: /{user}/{repo} -> localhost:{port}
# Managed by deploy script - do not edit manually

# Default: show deployment index
server {
    listen 80 default_server;
    server_name _;

    location / {
        root /opt/deployments/www;
        index index.html;
        try_files $uri $uri/ =404;
    }

    # Include per-app proxy configs
    include /opt/deployments/nginx/*.conf;
}
NGINXCONF""",

            # Create nginx config directory and index page
            "mkdir -p /opt/deployments/nginx",
            "mkdir -p /opt/deployments/www",
            """cat > /opt/deployments/www/index.html << 'INDEXHTML'
<!DOCTYPE html>
<html><head><title>CI/CD Deploy Server</title></head>
<body>
<h1>CI/CD Deploy Server</h1>
<p>Deployed applications will appear at /{user}/{repo}</p>
</body></html>
INDEXHTML""",

            # Remove default nginx server block to avoid conflict
            "rm -f /etc/nginx/conf.d/default.conf",
            "nginx -t && systemctl reload nginx",
        )

        # ──────────────────────────────────────────────
        # 7. EC2 Instance (t3.micro, Amazon Linux 2023)
        # ──────────────────────────────────────────────
        instance = ec2.Instance(self, "DeployInstance",
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ec2.AmazonLinuxImage(
                generation=ec2.AmazonLinuxGeneration.AMAZON_LINUX_2023,
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=sg,
            role=role,
            key_pair=key_pair,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(20),  # 20GB storage
                )
            ],
        )

        # ──────────────────────────────────────────────
        # 8. Outputs
        # ──────────────────────────────────────────────
        CfnOutput(self, "InstancePublicIp",
            value=instance.instance_public_ip,
            description="EC2 Public IP",
        )
        CfnOutput(self, "InstancePublicDns",
            value=instance.instance_public_dns_name,
            description="EC2 Public DNS",
        )
        CfnOutput(self, "ArtifactBucketName",
            value=artifact_bucket.bucket_name,
            description="S3 Bucket for build artifacts",
        )
        CfnOutput(self, "KeyPairId",
            value=key_pair.key_pair_id,
            description="Key Pair ID (download private key from AWS Console > EC2 > Key Pairs)",
        )
