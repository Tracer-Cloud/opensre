[![Linter Check](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/cargo-clippy.yml/badge.svg?branch=main)](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/cargo-clippy.yml) [![Tests](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/cargo-test.yml/badge.svg?branch=main)](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/cargo-test.yml) [![Release](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/dev-cross-platform-release-s3.yml/badge.svg?branch=main)](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/dev-cross-platform-release-s3.yml)
[![Dependency Security](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/cargo-audit.yml/badge.svg?branch=main)](https://github.com/Tracer-Cloud/tracer-client/actions/workflows/cargo-audit.yml)

<h2 align="left">
Tracer Linux Agent: Observability for Scientific HPC Workloads
</h2>

![Tracer-Banner](https://github.com/user-attachments/assets/5bbbdcee-11ca-4f09-b042-a5259309b7e4)

## What Is Tracer and Why Use It?

- Tracer is a system-level monitoring platform purpose-built for scientific computing. It is a a one-line install Linux agent and instant dashboards to give you insights into pipeline performance and cost optimization.

- Unlike industry agnostic monitoring agents, Tracer structures DevOps data for scientific pipelines, providing clear visibility into pipeline stages and execution runs. In environments like AWS Batch, where processes and containers are loosely connected, users struggle to understand which processes belong to which pipeline run, and frequently lose logs from failed containers, making debugging difficult.

- Tracer solves this by intelligently organizing and labeling pipelines, execution runs, and steps. Because it runs directly on Linux, it requires no code changes and supports any programming language, unlike point solutions that work only with one framework. This makes integration effortless even across multi-workload IT environments, including AlphaFold, Slurm, Airflow, Nextflow and also local Bash scripts.

- Architected for regulated industries, it ensures enterprise-grade security, with data never leaving your infrastructure, which is not the case with solutions such as DataDog.

<br />

![image](https://s15obc311h0vrt01.public.blob.vercel-storage.com/Github%20Readme%20Preview%20Tracer.png)

<br />

## Key Features

New metrics that help you speed up your pipelines and maximize your budget:

- Time and cost per dataset processed
- Execution duration and bottleneck identification for each pipeline step
- Cost attribution across pipelines, teams, and environments (dev, CI/CD, prod)
  Overall, making sense of scientific toolchains with poor/no observability.

<br />

## Get Started

### 1. Access the Sandbox

The easiest way to get started with Tracer is via our **browser-based sandbox**:  
👉 [https://sandbox.tracer.cloud/](https://sandbox.tracer.cloud/)

- Click **“Proceed to Onboarding”** to launch a guided onboarding experience tailored to your preferred tech stack — _no AWS credentials or setup required_.

### 2. Install Tracer With One Line of Code

Choose your preferred tech stack from the left-hand menu.

Copy the pre-filled curl command (also shown in the Sandbox) and run it in your terminal:

```bash
curl -sSL https://install.tracer.cloud | sh
```

### 3. Initialize the Tracer Client (requires token)

Start the Tracer client to initialize a pipeline and enable monitoring. You must provide your Tracer token every time you initialize.

- Get your token from the [Tracer Sandbox](https://sandbox.tracer.cloud/)
- Then run:

> **Note:** Root privileges required

```bash
sudo tracer init --token <paste-your-token-here> --watch-dir "/tmp/tracer"
```

### 4. Initialize a Pipeline

You can now choose to run any pipeline you want or use 'tracer test' to launch a prepared pipeline.
Run your own pipeline by following your usual workflow or try with one of our test examples first (https://github.com/Tracer-Cloud/nextflow-test-pipelines):

```bash
sudo -E tracer demo
```

### 5. Monitor Your Pipeline With Our Dashboard

Access the Tracer monitoring dashboard to watch your pipeline in action, including:

- Real-time execution metrics
- Pipeline stages
- Resource usage across runs

- The sandbox will guide you to your personal dashboard at the bottom of the onboarding page.

<br />

## Mission

> _"The goal of Tracer's Rust agent is to equip scientists and engineers with DevOps intelligence to efficiently harness massive computational power for humanity's most critical challenges."_
