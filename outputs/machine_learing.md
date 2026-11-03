# Building a Real-Time Anomaly Detection System with Machine Learning

## Introduction

# Introduction

In an era where data streams flow continuously from sensors, logs, and user interactions, spotting anomalies in real time can be the difference between averting a costly outage and watching a disaster unfold. This guide walks you through the end‑to‑end process of building a robust, production‑ready anomaly detection pipeline using modern machine‑learning techniques. We'll explore the underlying theory, walk through a hands‑on implementation in Python, and cover best practices for deployment and monitoring. By the end, you'll have a reusable framework that can be adapted to a wide range of domains—from cybersecurity to IoT—and the confidence to integrate real‑time intelligence into your own systems.

## Understanding Anomaly Detection and Preparing the Data

Anomaly detection identifies observations that deviate from expected patterns.  
Key concepts:

| Concept | Definition | Typical Use‑Case |
|---------|------------|------------------|
| **Point anomaly** | A single data point that is far from the rest of the data. | Fraud detection in credit card transactions. |
| **Collective anomaly** | A sequence of points that together form an unusual pattern. | Server CPU spikes indicating a DDoS attack. |
| **Contextual anomaly** | An observation that is normal in one context but abnormal in another. | Temperature reading of 30 °C is normal in summer but anomalous in winter. |

### Evaluation Metrics

| Metric | Formula | When to use |
|--------|---------|-------------|
| **Precision** | TP / (TP + FP) | High‑cost false positives (e.g., medical diagnosis). |
| **Recall** | TP / (TP + FN) | High‑cost false negatives (e.g., fraud prevention). |
| **F1‑Score** | 2·(Precision·Recall)/(Precision+Recall) | Balance between precision and recall. |
| **ROC‑AUC** | Area under ROC curve | Threshold‑independent performance. |
| **PR‑AUC** | Area under precision‑recall curve | Useful when positives are rare. |

### Data Preprocessing for Time‑Series Anomaly Detection

1. **Cleaning**
   - Remove or impute missing values (`NaN`, `NULL`).
   - Detect and correct outliers that are measurement errors (not true anomalies).
   - Standardize timestamps (e.g., UTC, consistent granularity).

2. **Feature Engineering**
   - **Statistical features**: rolling mean, std, min, max, percentiles.
   - **Temporal features**: hour of day, day of week, lagged values.
   - **Domain‑specific indicators**: moving averages, trend slopes, seasonality indices.

3. **Scaling & Normalization**
   - Apply `StandardScaler` or `MinMaxScaler` to stabilize variance across features.

4. **Splitting Time‑Series Data**
   - **Training set**: earliest segment (e.g., first 70 % of timeline).
   - **Validation set**: subsequent segment (next 15 %).
   - **Test set**: final segment (remaining 15 %).
   - Ensure no leakage by keeping temporal order intact.

5. **Windowing for Collective Anomalies**
   - Slide a fixed‑size window over the series.
   - Label windows as anomalous if any point inside is anomalous.

6. **Handling Imbalanced Labels**
   - Use techniques like SMOTE for synthetic minority oversampling or class‑weight adjustments in loss functions.

By following these steps, you create a clean, well‑structured dataset that supports robust anomaly detection models and reliable evaluation.

## Designing the Model Architecture

For real‑time anomaly detection on streaming sensor data, a **transformer‑based encoder** offers the best trade‑off between expressiveness and latency. Unlike LSTMs, transformers process the entire window in parallel, reducing per‑sample inference time, while self‑attention captures long‑range dependencies that often signal subtle anomalies.

### Model Selection Rationale
| Criterion | Transformer Encoder | LSTM Autoencoder |
|-----------|---------------------|------------------|
| **Parallelism** | ✔ | ✖ |
| **Long‑range capture** | ✔ | ✖ |
| **Training speed** | ✔ | ✖ |
| **Memory footprint** | ↑ | ↓ |
| **Inference latency** | ↓ | ↑ |

The encoder is paired with a lightweight decoder that reconstructs the input window. Reconstruction error is the anomaly score; high error indicates deviation from learned normal patterns.

### Architecture Diagram (ASCII)

```
Input Window (T, D) ──► Positional Encoding ──►
   ┌───────────────────────────────────────────────┐
   │  Transformer Encoder (N layers, H heads)      │
   └───────────────────────────────────────────────┘
                     │
                     ▼
          Latent Representation (Z)
                     │
                     ▼
   ┌───────────────────────────────────────────────┐
   │  Decoder (Linear + ReLU + Linear)              │
   └───────────────────────────────────────────────┘
                     │
                     ▼
Reconstructed Window (T, D)
```

### Loss Function
The primary objective is reconstruction fidelity:

\[
\mathcal{L}_{\text{rec}} = \frac{1}{T}\sum_{t=1}^{T}\|x_t - \hat{x}_t\|_2^2
\]

Optionally, a **KL‑divergence** term can regularise the latent space if a variational encoder is used:

\[
\mathcal{L}_{\text{KL}} = D_{\text{KL}}\big(\mathcal{N}(\mu,\sigma^2)\,\|\,\mathcal{N}(0, I)\big)
\]

The total loss is \(\mathcal{L} = \mathcal{L}_{\text{rec}} + \beta \mathcal{L}_{\text{KL}}\) (β ≈ 0.01).

### Hyper‑parameter Considerations
| Hyper‑parameter | Typical Range | Impact |
|-----------------|---------------|--------|
| `embed_dim` (H) | 64–256 | Higher → richer representations, ↑ latency |
| `num_layers` (N) | 2–4 | More layers → better context capture |
| `num_heads` | 4–8 | Improves parallel attention |
| `dropout` | 0.1–0.3 | Regularises, prevents over‑fitting |
| `learning_rate` | 1e‑4–1e‑3 | Controls convergence speed |
| `window_size` (T) | 32–128 | Balances temporal context vs. latency |

Early stopping on validation reconstruction loss and a sliding‑window evaluation mitigate over‑fitting to transient noise.

### PyTorch Model Definition

```python
import torch
import torch.nn as nn
import math

class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=500):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() *
                             (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        self.register_buffer('pe', pe.unsqueeze(0))

    def forward(self, x):
        return x + self.pe[:, :x.size(1)]

class TransformerAutoencoder(nn.Module):
    def __init__(self, input_dim, embed_dim=128, num_heads=4,
                 num_layers=3, dropout=0.1):
        super().__init__()
        self.input_proj = nn.Linear(input_dim, embed_dim)
        encoder_layer = nn.TransformerEncoderLayer(d_model=embed_dim,
                                                   nhead=num_heads,
                                                   dropout=dropout)
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers)
        self.decoder = nn.Sequential(
            nn.Linear(embed_dim, embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, input_dim)
        )
        self.pos_enc = PositionalEncoding(embed_dim)

    def forward(self, x):
        # x: (batch, seq_len, feat)
        x = self.input_proj(x)          # (B, T, E)
        x = self.pos_enc(x)             # (B, T, E)
        x = x.transpose(0, 1)           # (T, B, E) for transformer
        z = self.encoder(x)             # (T, B, E)
        z = z.transpose(0, 1)           # (B, T, E)
        recon = self.decoder(z)         # (B, T, feat)
        return recon
```

This concise implementation captures the core of a transformer‑based anomaly detector, ready for integration into a streaming pipeline.

## Implementing the End‑to‑End Pipeline

1. **Data loading**  
   *Read raw sensor data (CSV, Parquet, etc.) into a Pandas DataFrame.*  
   ```python
   import pandas as pd

   # Replace with your path
   df = pd.read_csv("sensor_readings.csv")
   # Assume a timestamp column and a value column
   df["timestamp"] = pd.to_datetime(df["timestamp"])
   df.set_index("timestamp", inplace=True)
   ```

2. **Preprocessing pipeline**  
   *Handle missing values, normalize, and create lag features.*  
   ```python
   import numpy as np
   from sklearn.preprocessing import StandardScaler

   # Forward‑fill missing values
   df = df.fillna(method="ffill")

   # Normalization
   scaler = StandardScaler()
   df["value_norm"] = scaler.fit_transform(df[["value"]])

   # Create lag features for a 5‑step window
   for lag in range(1, 6):
       df[f"lag_{lag}"] = df["value_norm"].shift(lag)
   df.dropna(inplace=True)
   ```

3. **Model training loop**  
   *Use a simple LSTM for anomaly detection. The loss is reconstruction error.*  
   ```python
   import torch
   import torch.nn as nn
   from torch.utils.data import DataLoader, TensorDataset

   class AnomalyLSTM(nn.Module):
       def __init__(self, input_dim, hidden_dim, num_layers=1):
           super().__init__()
           self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
           self.fc = nn.Linear(hidden_dim, input_dim)

       def forward(self, x):
           out, _ = self.lstm(x)
           out = self.fc(out[:, -1, :])  # last time step
           return out

   # Prepare tensors
   X = df[[f"lag_{i}" for i in range(1, 6)]].values.astype(np.float32)
   y = df["value_norm"].values.astype(np.float32).reshape(-1, 1)

   dataset = TensorDataset(torch.from_numpy(X), torch.from_numpy(y))
   loader = DataLoader(dataset, batch_size=64, shuffle=True)

   model = AnomalyLSTM(input_dim=5, hidden_dim=32)
   criterion = nn.MSELoss()
   optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)

   epochs = 20
   for epoch in range(epochs):
       for xb, yb in loader:
           pred = model(xb)
           loss = criterion(pred, yb)
           optimizer.zero_grad()
           loss.backward()
           optimizer.step()
       print(f"Epoch {epoch+1}/{epochs} – Loss: {loss.item():.4f}")
   ```

4. **Inference with sliding windows**  
   *Apply the model over the full series using a sliding window of length 5.*  
   ```python
   window_size = 5
   preds = []
   with torch.no_grad():
       for i in range(len(df) - window_size + 1):
           window = df.iloc[i:i+window_size][[f"lag_{j}" for j in range(1, 6)]].values
           window_tensor = torch.from_numpy(window.astype(np.float32))
           pred = model(window_tensor)
           preds.append(pred.item())

   # Align predictions with timestamps (use the last timestamp in each window)
   pred_series = pd.Series(preds, index=df.index[window_size-1:])
   ```

5. **Thresholding strategy for anomaly scoring**  
   *Compute reconstruction error and flag values above a percentile threshold.*  
   ```python
   # Reconstruction error
   errors = np.abs(pred_series - df["value_norm"].iloc[window_size-1:])

   # 95th percentile as threshold
   threshold = np.percentile(errors, 95)
   anomalies = errors > threshold

   # Visual check
   import matplotlib.pyplot as plt
   plt.figure(figsize=(12, 4))
   plt.plot(df.index[window_size-1:], errors, label="Error")
   plt.axhline(threshold, color="red", linestyle="--", label="Threshold")
   plt.scatter(df.index[window_size-1:][anomalies], errors[anomalies], color="red", label="Anomaly")
   plt.legend()
   plt.show()
   ```

**Key takeaways**

- The pipeline starts with clean data loading, followed by a reproducible preprocessing step that normalizes and creates lag features.  
- A lightweight LSTM autoencoder learns to reconstruct normal patterns; its reconstruction error becomes the anomaly score.  
- Sliding‑window inference ensures every timestamp is evaluated in context.  
- A percentile‑based threshold adapts to the data distribution, providing a simple yet effective anomaly flagging mechanism.

This end‑to‑end example is fully runnable, uses only standard Python libraries, and can be extended to more complex models or streaming scenarios.

## Deploying, Scaling, and Monitoring the System

### Deployment Options  
| Option | Strengths | Typical Use‑Case |
|--------|-----------|------------------|
| **Docker** | Portable, reproducible images, easy CI/CD | Single‑service ML model, edge devices |
| **Kubernetes** | Auto‑scaling, rolling updates, service mesh | Multi‑service pipelines, production clusters |
| **Serverless (AWS Lambda, Azure Functions)** | Pay‑per‑invocation, zero ops | Low‑latency micro‑inference, event‑driven workloads |

A common pattern is to containerise the model with Docker, push the image to a registry, and deploy it to a Kubernetes cluster that manages replicas and load balancing. For bursty traffic, a serverless layer can be added to spin up temporary containers.

### Real‑Time Inference Serving  
Frameworks such as **FastAPI**, **TensorFlow Serving**, or **TorchServe** expose a REST/GRPC endpoint. The service should:

1. Load the model once at startup.
2. Batch incoming requests when latency allows.
3. Use GPU or TPU accelerators if available.

Example endpoint (FastAPI):

```python
from fastapi import FastAPI
import torch
from transformers import pipeline

app = FastAPI()
model = pipeline("sentiment-analysis")

@app.post("/predict")
async def predict(text: str):
    result = model(text)
    return {"label": result[0]["label"], "score": result[0]["score"]}
```

### Scaling Considerations  
* **Horizontal Pod Autoscaler (HPA)** in Kubernetes scales replicas based on CPU, memory, or custom metrics (e.g., request latency).  
* **Queue‑Based Scaling**: Use a message broker (Kafka, SQS) to buffer requests, letting the inference service process at a steady rate.  
* **Resource Requests/Limits**: Define `cpu` and `memory` to prevent node starvation and enable efficient scheduling.

### Monitoring Metrics  
| Metric | Target | Alert |
|--------|--------|-------|
| **Latency (p99)** | < 200 ms | `latency > 200ms` |
| **False‑Positive Rate** | < 5 % | `false_positive_rate > 0.05` |
| **CPU Utilisation** | < 80 % | `cpu > 80%` |
| **Memory Utilisation** | < 90 % | `memory > 90%` |

Prometheus scrapes `/metrics` from the FastAPI app (via `prometheus_fastapi_instrumentator`). A sample alert rule:

```yaml
groups:
- name: inference
  rules:
  - alert: HighLatency
    expr: histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket[5m])) by (le)) > 0.2
    for: 1m
    labels:
      severity: warning
    annotations:
      summary: "Inference latency exceeds 200 ms"
      description: "p99 latency is {{ $value }}s"
```

### Minimal Dockerfile  

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The `requirements.txt` should include `fastapi`, `uvicorn`, `torch`, `transformers`, and `prometheus_fastapi_instrumentator`.

With these components—containerised deployment, autoscaling, and robust metrics—you can reliably serve ML models at scale while staying alert to performance regressions.

## Conclusion

An effective anomaly‑detection pipeline starts by clearly distinguishing the anomaly type—point, collective, or contextual—and selecting evaluation metrics that reflect the business cost of false positives versus false negatives. Precision, recall, F1‑score, ROC‑AUC, and PR‑AUC together give a nuanced picture of model performance, especially when anomalies are rare. Robust preprocessing—cleaning missing values, engineering statistical and temporal features, scaling, and respecting temporal order in train/validation/test splits—lays the foundation for reliable detection. Window‑based labeling and strategies for imbalanced data, such as SMOTE or class‑weighted losses, further ensure that the model learns true abnormal patterns rather than noise.

For real‑time streaming data, a transformer‑based encoder‑decoder architecture outperforms traditional LSTM autoencoders by leveraging parallel processing and self‑attention to capture long‑range dependencies with lower latency. The encoder learns a compact representation of normal behavior, while the decoder’s reconstruction error serves as a precise anomaly score. Coupled with the disciplined data preparation steps outlined above, this design delivers a scalable, high‑fidelity solution that can be evaluated confidently across precision‑focused, recall‑focused, and threshold‑independent metrics.
