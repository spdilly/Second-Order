"""
Property analysis skill — Section 8 SFH automated underwriting.

Joe Berlin engagement (May 2026).

Pipeline:
  analyze(address, price) -> result
    1. normalize address
    2. look up FMR rent (fmr.py)
    3. look up property tax (lookup.py priority chain)
    4. compute returns (compute.py)
    5. populate Excel proforma (populate.py)
    6. render markdown report (report.py)
"""
