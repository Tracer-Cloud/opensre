"""Getting output to a destination outside the process.

``notifications`` delivers alerts and background results to a chat channel;
``reporting`` delivers scheduled reports. Both are vendor-neutral: a channel
registers an adapter, and callers name a channel rather than a vendor.
"""
