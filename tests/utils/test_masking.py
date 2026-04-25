"""Unit tests for data masking utilities."""

import pytest
from app.masking import MaskingContext
from app.masking.detectors import detect_pii_entities, detect_infrastructure_ids


def test_masking_context_basic():
    """Test basic masking and unmasking flow."""
    ctx = MaskingContext()
    original = "My IP is 192.168.1.1 and my database is db-prod-01"
    
    masked = ctx.mask(original)
    assert "192.168.1.1" not in masked
    assert "db-prod-01" not in masked
    assert "MASKED_IP" in masked or "MASKED_" in masked
    
    unmasked = ctx.unmask(masked)
    assert unmasked == original


def test_masking_context_idempotency():
    """Test that masking the same value twice returns the same placeholder."""
    ctx = MaskingContext()
    val = "secret-key-123"
    
    placeholder1 = ctx.mask(val)
    placeholder2 = ctx.mask(val)
    
    assert placeholder1 == placeholder2


def test_detect_pii_entities():
    """Test detection of PII (Email, IP)."""
    text = "Contact me at test@example.com or 10.0.0.5"
    entities = list(detect_pii_entities(text))
    
    types = [e.entity_type for e in entities]
    assert "EMAIL" in types
    assert "IP_ADDRESS" in types


def test_detect_infrastructure_ids():
    """Test detection of infrastructure identifiers (AWS ARNs, etc.)."""
    text = "Check arn:aws:ec2:us-east-1:123456789012:instance/i-1234567890abcdef0"
    entities = list(detect_infrastructure_ids(text))
    
    assert any(e.entity_type == "AWS_ARN" for e in entities)


def test_mask_value_recursive():
    """Test masking complex data structures (lists, dicts)."""
    ctx = MaskingContext()
    data = {
        "user": "alice@example.com",
        "metadata": {
            "ip": "1.1.1.1",
            "tags": ["prod", "arn:aws:s3:::my-bucket"]
        }
    }
    
    masked = ctx.mask_value(data)
    
    assert "@example.com" not in str(masked)
    assert "1.1.1.1" not in str(masked)
    assert "arn:aws:s3" not in str(masked)
    
    unmasked = ctx.unmask_value(masked)
    assert unmasked == data
