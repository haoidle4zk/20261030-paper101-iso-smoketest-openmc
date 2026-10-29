"""Mô hình OpenMC Paper 10.1 (PNE 2022, Fig. 1 & Section 2.2) — lấy nguyên từ project cũ
projects/2026.17.10_Paper10.1_4_tool/Openmc_mac/legacy_suite_v1/openmc_model.py (hình học, vật liệu, nguồn, tally),
chỉ thêm hàm run() để mỗi job tự chạy và ghi result.csv cho goat.

- Nguồn photon điểm đẳng hướng tại z = 0, đơn năng E0 (GOAT 2026-10-30, trước là chùm song song +z)
- Ống chuẩn trực Pb z = 10..50 cm (lỗ r = 0.5, vỏ r = 3.0)
- Mẫu thủy tinh z = 50..50+x cm (r = 5.0)
- Vùng đo (F4 tương đương) z = 70..72 cm (r = 5.0, V = 157.0796 cm3)
- Vỏ chì ngoài z = 45..75 cm (r_in = 8, r_out = 10)
- Tally: total flux và peak flux (uncollided, E0 ± 1%) trong vùng đo, chia cho thể tích
"""
import csv
import os
from pathlib import Path

import openmc

DET_VOLUME_CM3 = 157.07963268

COMPOSITION = {
    "S1": {"density": 2.390, "comp": {"Li": 0.18583, "B": 0.13976, "O": 0.60120, "Al": 0.02646, "Si": 0.04674}},
    "S2": {"density": 2.725, "comp": {"Li": 0.18583, "B": 0.12423, "O": 0.57335, "Al": 0.02646, "Si": 0.04674, "Gd": 0.04338}},
    "S3": {"density": 2.908, "comp": {"Li": 0.18583, "B": 0.10870, "O": 0.54550, "Al": 0.02646, "Si": 0.04674, "Gd": 0.08676}},
    "S4": {"density": 3.103, "comp": {"Li": 0.18583, "B": 0.09317, "O": 0.51765, "Al": 0.02646, "Si": 0.04674, "Gd": 0.13014}},
    "S5": {"density": 3.836, "comp": {"B": 0.18635, "O": 0.52192, "Si": 0.04674, "Ca": 0.07147, "Gd": 0.17352}},
    "S6": {"density": 3.909, "comp": {"B": 0.17082, "O": 0.49407, "Si": 0.04674, "Ca": 0.07147, "Gd": 0.21690}},
    "S7": {"density": 4.181, "comp": {"B": 0.15529, "O": 0.46622, "Si": 0.04674, "Ca": 0.07147, "Gd": 0.26028}},
    "S8": {"density": 4.411, "comp": {"B": 0.13976, "O": 0.43837, "Si": 0.04674, "Ca": 0.07147, "Gd": 0.30366}},
}


def build_materials(sample: str):
    pb = openmc.Material(name="Lead")
    pb.add_element("Pb", 1.0)
    pb.set_density("g/cm3", 11.34)
    mats, sample_mat = [pb], None
    if sample.upper() != "BLANK":
        prop = COMPOSITION[sample.upper()]
        sample_mat = openmc.Material(name=sample.upper())
        sample_mat.set_density("g/cm3", prop["density"])
        for el, frac in prop["comp"].items():
            sample_mat.add_element(el, frac, percent_type="wo")
        mats.append(sample_mat)
    return openmc.Materials(mats), pb, sample_mat


def build_geometry(thickness_cm: float, pb_mat, sample_mat=None):
    s_world = openmc.Sphere(x0=0.0, y0=0.0, z0=40.0, r=100.0, boundary_type="vacuum")
    z11, z12 = openmc.ZPlane(z0=10.0), openmc.ZPlane(z0=50.0)
    z13 = openmc.ZPlane(z0=50.0 + max(float(thickness_cm), 1e-6))
    z14, z15, z16, z17 = openmc.ZPlane(z0=45.0), openmc.ZPlane(z0=70.0), openmc.ZPlane(z0=72.0), openmc.ZPlane(z0=75.0)
    r20, r21, r22, r23, r24 = (openmc.ZCylinder(r=r) for r in (0.5, 3.0, 5.0, 8.0, 10.0))

    coll_pb = openmc.Cell(name="Collimator_Pb", fill=pb_mat, region=+z11 & -z12 & +r20 & -r21)
    coll_bore = openmc.Cell(name="Collimator_Bore", region=+z11 & -z12 & -r20)
    sample_cell = openmc.Cell(name="Sample", fill=sample_mat, region=+z12 & -z13 & -r22)
    detector = openmc.Cell(name="Detector", region=+z15 & -z16 & -r22)
    shield_pb = openmc.Cell(name="Shield_Pb", fill=pb_mat, region=+z14 & -z17 & +r23 & -r24)
    chamber = openmc.Cell(name="Chamber", region=+z14 & -z17 & -r23 & ~coll_pb.region & ~coll_bore.region
                          & ~sample_cell.region & ~detector.region)
    outer = openmc.Cell(name="OuterVoid", region=-s_world & ~shield_pb.region & ~chamber.region & ~coll_pb.region
                        & ~coll_bore.region & ~sample_cell.region & ~detector.region)
    return openmc.Geometry([coll_pb, coll_bore, sample_cell, detector, shield_pb, chamber, outer]), detector


def build_settings(energy_kev: float, n_particles: int, n_batches: int = 10):
    st = openmc.Settings()
    st.run_mode = "fixed source"
    st.batches = int(n_batches)
    st.particles = int(n_particles // n_batches)
    src = openmc.IndependentSource()
    src.space = openmc.stats.Point((0.0, 0.0, 0.0))
    src.angle = openmc.stats.Isotropic()  # nguồn điểm đẳng hướng như bài báo (mục 2.2)
    src.energy = openmc.stats.Discrete([float(energy_kev) * 1e3], [1.0])
    src.particle = "photon"
    st.source = src
    st.photon_transport = True
    st.electron_treatment = "ttb"
    st.cutoff = {"energy_photon": 1000.0}
    return st


def build_tallies(detector, energy_kev: float):
    e0 = float(energy_kev) * 1e3
    cf, pf = openmc.CellFilter(detector), openmc.ParticleFilter(["photon"])
    tot = openmc.Tally(name="total_flux")
    tot.filters = [cf, pf]
    tot.scores = ["flux"]
    peak = openmc.Tally(name="peak_flux")
    peak.filters = [cf, pf, openmc.EnergyFilter([e0 * 0.99, e0 * 1.01])]
    peak.scores = ["flux"]
    return openmc.Tallies([tot, peak])


def run(sample: str, thickness_cm: float, energy_kev: float, primaries: int, batches: int = 10):
    """Dựng model trong thư mục hiện tại, chạy OpenMC, ghi result.csv (1 dòng). Lỗi => exception (không ghi số giả)."""
    mats, pb, smat = build_materials(sample)
    geo, det = build_geometry(thickness_cm, pb, smat)
    model = openmc.model.Model(geometry=geo, materials=mats,
                               settings=build_settings(energy_kev, int(primaries), batches),
                               tallies=build_tallies(det, energy_kev))
    sp_path = model.run(threads=os.cpu_count(), output=False)
    with openmc.StatePoint(sp_path) as sp:
        t, p = sp.get_tally(name="total_flux"), sp.get_tally(name="peak_flux")
        row = {
            "peak_value": float(p.mean.flatten()[0]) / DET_VOLUME_CM3,
            "peak_std": float(p.std_dev.flatten()[0]) / DET_VOLUME_CM3,
            "total_value": float(t.mean.flatten()[0]) / DET_VOLUME_CM3,
            "total_std": float(t.std_dev.flatten()[0]) / DET_VOLUME_CM3,
            "batches": batches,
        }
    with open(Path.cwd() / "result.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        w.writeheader()
        w.writerow(row)
    return row
