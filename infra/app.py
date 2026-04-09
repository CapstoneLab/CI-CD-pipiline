#!/usr/bin/env python3
import aws_cdk as cdk

from infra.infra_stack import InfraStack

app = cdk.App()
InfraStack(app, "CiCdDeployStack",
    env=cdk.Environment(account="668568918251", region="us-east-1"),
)

app.synth()
