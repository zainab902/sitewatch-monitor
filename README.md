# 🌐 SiteWatch: Cloud Uptime & Incident Monitor

SiteWatch is a hybrid serverless observability system designed to monitor web endpoint availability, response latency, and incident lifecycle transitions. It pairs an AWS Lambda probing engine with an interactive Streamlit dashboard to contrast cloud datacenter network latency against local client connections.

---

## 🚀 Key Features

* **Dual-Source Health Checks:** Executes probes both locally and via serverless AWS Lambda invocations to evaluate network routing and geographic latency variations.
* **Response-Time Telemetry:** Captures round-trip execution durations (in milliseconds) and plots continuous time-series trend lines.
* **Uptime Calculation:** Automatically derives rolling availability percentages based on logged success and failure states.
* **Incident Detection:** Tracks HTTP status codes (e.g., 200 OK vs. 5xx Server Errors / Connection Timeouts) and logs state transitions in an incident audit table.
* **CLI-Driven Serverless Execution:** Configured and deployed via the AWS CLI with least-privilege IAM access.

---

## 📊 Observations: Cloud vs. Local Latency

SiteWatch demonstrates the measurable network benefit of running telemetry probes inside cloud infrastructure:

| Probing Source | Typical Latency | Notes |
| :--- | :--- | :--- |
| **AWS Lambda** | **~96 ms** | Direct low-hop datacenter backbone routing |
| **Local Client** | **~500 – 700 ms** | Standard residential ISP network traversal |

---

## 🛠️ Architecture Overview

```text
       +---------------------------------------------+
       |             Streamlit Dashboard             |
       |  (Uptime Metrics, Trend Charts, Incident Log)|
       +----------------------+----------------------+
                              |
              +---------------+---------------+
              |                               |
       [Local Probing]                 [Cloud Probing]
              |                               |
              v                               v
    (Direct HTTP Request)            (AWS CLI / boto3 Invoke)
              |                               |
              |                               v
              |                       +---------------+
              |                       |  AWS Lambda   |
              |                       +-------+-------+
              |                               |
              +---------------+---------------+
                              |
                              v
                  +-----------------------+
                  | Monitored Endpoints   |
                  | (Google, GitHub, etc.)|
                  +-----------------------+
