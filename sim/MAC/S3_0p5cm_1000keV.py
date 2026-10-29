"""Job S3_0p5cm_1000keV: mẫu S3, bề dày 0.5 cm, năng lượng 1000 keV. Số hạt lấy từ project.yaml (primaries)."""
from model import run

run(sample="S3", thickness_cm=0.5, energy_kev=1000.0, primaries=@PRIMARIES@)
