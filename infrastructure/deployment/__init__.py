"""Building, shipping, and sizing a deployed OpenSRE.

``ec2`` drives the gateway AMI and systemd lifecycle, ``container`` installs the
main-channel binary when a container starts, ``packaging`` validates a
release wheel, and ``contracts`` holds the deployment shapes runtime code reads
(:class:`~infrastructure.deployment.contracts.models.SizeProfile` sizes the gateway
capacity gate).
"""
