"""A.I.M.S. Tool Warehouse — server-rendered public frontend.

Routes under /app. All catalog reads go through redact_payload(..., "tenant")
(Sacred Separation). No operator/admin routes are exposed here.
"""
