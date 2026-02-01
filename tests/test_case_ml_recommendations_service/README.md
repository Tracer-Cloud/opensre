Here’s how to build a **simple MVP** of this Dressy pipeline on AWS with the smallest surface area, while keeping the same core shape: ingest product data, build customer profiles, train a model, serve it, and return recommendations.

## Business Objective

Ship an end to end recommender MVP that incorporates new products into recommendations within 12 hours.

## Scope

Product ingest and labeling, purchase event capture, nightly feature build, nightly training, versioned model deploy, online inference, basic monitoring.

## MVP Architecture on AWS

### 1) Ingest product uploads

**Goal:** get new dresses into a canonical store quickly.

* **S3** as the landing zone: `s3://dressy-raw/products/`
* Vendor uploads JSON + images to S3.
* **S3 event notification → Lambda** writes a row into a “products” table and enqueues a labeling job.

MVP stores:

* **DynamoDB** `Products` table for serving-time reads (fast, cheap).
* Optional: also dump raw events to **S3** for replay.

### 2) Product labeling pipeline (lightweight)

**Goal:** normalize messy vendor data into consistent attributes.

MVP approach:

* Lambda takes the image, calls **Amazon Rekognition** (labels, colors, maybe text), merges vendor metadata.
* Writes labeled product record to DynamoDB `Products` and to S3 `s3://dressy-features/products/` as parquet or JSONL.

Why Rekognition: zero infra, good enough, fast.

### 3) Capture purchase events

**Goal:** build user history.

* Frontend calls **API Gateway → Lambda** `POST /purchase`
* Lambda writes:

  * raw event to **Kinesis Data Firehose → S3** `s3://dressy-raw/purchases/`
  * and an aggregated record to DynamoDB `UserProfiles` (append or counters)

MVP: DynamoDB is your online feature store. S3 is your offline truth.

### 4) Build customer profiles and training dataset (nightly)

**Goal:** turn raw events into training examples.

* **EventBridge scheduled rule** runs nightly.
* Job runs in **AWS Glue** (Spark) or **ECS Fargate** container.

  * Reads `products` and `purchases` from S3
  * Produces:

    * `s3://dressy-features/user_profiles/`
    * `s3://dressy-training/training_set/`
  * Optionally updates DynamoDB `UserProfiles` in batch.

Pick one:

* Glue if you want managed ETL.
* ECS Fargate if you want simple Python you control.

### 5) Train model (nightly)

**Goal:** produce a new model artifact.

* Use **SageMaker Training Job** triggered after the dataset build.
* Training code reads from `s3://dressy-training/…`
* Outputs model to `s3://dressy-model-registry/models/<date or version>/`

For the recommender itself, keep it stupid-simple for MVP:

* Start with **two-tower retrieval** or even **matrix factorization**.
* Or go even simpler: build embeddings and do nearest neighbor search.

### 6) Evaluate and promote

**Goal:** prevent obviously bad models from shipping.

* After training, run a small **SageMaker Processing Job** or Lambda that:

  * computes a few metrics on a holdout set (top-k hit rate, precision@k)
  * writes metrics JSON to S3
  * decides whether to “promote”

Promotion mechanism MVP:

* Update an S3 pointer: `s3://dressy-model-registry/production/latest.tar.gz`
* Or store “production version” in **SSM Parameter Store**.

### 7) Serve recommendations (online)

**Goal:** low latency inference.

Two MVP options:

**Option A: SageMaker real-time endpoint**

* Deploy “latest” model to a **SageMaker Endpoint**.
* **Recommendations API** (API Gateway → Lambda) calls the endpoint.
* Lambda enriches with product details from DynamoDB `Products`.

**Option B: No SageMaker endpoint, just Lambda**

* If model is tiny (embeddings + ANN index), you can load it in Lambda from S3 at cold start.
* Use **OpenSearch k-NN** as your vector index (store product embeddings).
* Lambda query: user embedding → OpenSearch → top-k product IDs → DynamoDB lookup.

If you want minimum moving parts, start with **Option A**.

### 8) Frontend purchase service

* The “Purchasing Service” is basically your main backend.
* It calls:

  * `GET /recommendations?user_id=...`
  * `POST /purchase`

## Implementation Plan

### Phase 1: Working loop in 1 day

* S3 product upload
* Lambda labeling (Rekognition) → DynamoDB Products
* Purchase API → Firehose → S3 + DynamoDB UserProfiles
* Recommendations API returns “most popular” or “recently added”
  This gives you the plumbing and the UI integration.

### Phase 2: Nightly training in 2 to 3 days

* Nightly ETL to build training set
* SageMaker training job outputs model
* Basic evaluation + promotion
* SageMaker endpoint serving

### Phase 3: Make it not fragile in 1 to 2 days

* Add retries, DLQs, idempotency keys for purchase events
* CloudWatch dashboards and alarms
* Canary deploy for new model versions

## Success Criteria

* New product upload appears in recommendation candidates within 12 hours.
* Recommendations endpoint p95 latency under 300ms.
* Model deploy is versioned and reversible in under 5 minutes.
* At least one end to end alarm catches a broken pipeline before users notice.

## Out of Scope

* Near-real-time retraining
* Complex feature store governance
* Multi-region serving
* Human labeling workflows

If you tell me one thing: do you want “MVP” to mean **fewest AWS services** or **most managed**? I’ll lock the plan to a single stack (either “all serverless” or “SageMaker-centric”) and give you exact resources, names, and the event flow.
