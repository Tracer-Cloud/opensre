import os

PIPELINE_NAME = os.getenv("PIPELINE_NAME", "bioinformatics_variant_pipeline")
PIPELINE_RUN_ID = os.getenv("PIPELINE_RUN_ID", "default")
PIPELINE_STAGE = os.getenv("PIPELINE_STAGE", "ingest")

# Fields every variant record must have
REQUIRED_FIELDS = [
    "sample_id",
    "gene",
    "chromosome",
    "position",
    "ref_allele",
    "alt_allele",
    "quality_score",
]
