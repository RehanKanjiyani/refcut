"""refcut — reference-video analysis -> machine-executable editing blueprint.

Pipeline (prototype 1):
    reference video -> measured evidence (cuts, beats, motion, flashes...)
                    -> semantic analysis (any VLM / the analysis prompt)
                    -> timeline-level editing blueprint (JSON + markdown)
"""

__version__ = "0.1.0"
