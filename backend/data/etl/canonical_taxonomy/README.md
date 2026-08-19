# Canonical Product×Intent Taxonomy

This directory contains the numbered taxonomy transformation lineage and its
generated artifacts. The active reconciler reads the rectified artifact and the
`1c` intent-mapping rules from here, but does not rewrite these files.

Run a taxonomy stage only as part of a deliberate source-data rebuild. After a
taxonomy change, regenerate active staging with
`../active/scripts/5_reconcile_tri_angle_stage.py` before ingesting it.
