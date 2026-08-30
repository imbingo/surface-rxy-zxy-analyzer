"""Generate a deterministic Precitec-style wafer-on-cold-plate area-scan demo."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "V4.5.4_Precitec_80mm_Wafer_on_ColdPlate_Demo.dat"
PREVIEW_PATH = ROOT / "V4.5.4_Precitec_80mm_Wafer_on_ColdPlate_preview.png"


def main():
    rng = np.random.default_rng(454)
    pitch_mm = 0.5
    axis = np.arange(-40.0, 40.0 + pitch_mm / 2.0, pitch_mm)
    points_per_line = len(axis)
    number_of_lines = len(axis)
    rows = []
    preview_x, preview_y, preview_z = [], [], []

    for row_index, y in enumerate(axis):
        scan_x = axis if row_index % 2 == 0 else axis[::-1]
        for col_index, x in enumerate(scan_x):
            radius = float(np.hypot(x, y))
            encoder_x = int(round(x * 1000.0))
            encoder_y = int(round(y * 1000.0))
            encoder_z = 12000 + int(round(20.0 * np.sin(x / 8.0)))
            intensity = 4.2 + 0.3 * np.cos(radius / 9.0) + rng.normal(0.0, 0.04)
            in_aperture = radius <= 40.0
            in_wafer = radius <= 15.0
            if not in_aperture:
                thickness_text = "No Data"
            else:
                plate_um = (250.0 + 0.012 * x - 0.009 * y
                            + 0.45 * np.sin(x / 11.0) * np.cos(y / 13.0)
                            + rng.normal(0.0, 0.08))
                if in_wafer:
                    xn, yn = x / 15.0, y / 15.0
                    bow_um = 6.0 * (xn * xn + yn * yn)
                    warpage_um = 2.8 * (xn * xn - yn * yn) + 1.6 * xn * yn
                    chuck_bump_um = 1.8 * np.exp(-((x - 3.0) ** 2 + (y + 2.0) ** 2) / 28.0)
                    surface_um = (plate_um + 1000.0 + bow_um + warpage_um
                                  + chuck_bump_um + rng.normal(0.0, 0.10))
                else:
                    surface_um = plate_um
                thickness_text = f"{surface_um:.5f}"
                preview_x.append(x); preview_y.append(y); preview_z.append(surface_um)
            rows.append(
                f"1;{encoder_z};{encoder_y};{encoder_x};{thickness_text};"
                f"{intensity:.4f};{x:.4f};{y:.4f};")

    header = [
        "Precitec Optronik - FSS Explorer v2.749 - SCAN PATH DATA;",
        "ScanProgram: <PrecitecFSSExplorer>; Demo: 80 mm aperture wafer on cold plate;",
        "Gain Correction X:1.00000000, Y:1.00000000; Applied Transformation ShiftX:0.00000000, ShiftY:0.00000000, Rotation (rad):0.00000000",
        f"#Object: AreaScan; PointsPerLine: {points_per_line}; NumberOfLines: {number_of_lines}; PercentileFilter: 50.00",
        "#Attention: Encoder X/Y values are external axis corrected values",
        "# real scanner position (X/Y, ENC X/Y) - external axis position",
        "#Encoder V;Encoder Z;Encoder Y;Encoder X;Thickness 1;Intensity;X Pos [mm];Y Pos [mm]",
    ]
    DATA_PATH.write_text("\n".join(header + rows) + "\n", encoding="utf-8")

    fig, ax = plt.subplots(figsize=(8.5, 7.2), constrained_layout=True)
    scatter = ax.scatter(preview_x, preview_y, c=preview_z, s=8, cmap="turbo", edgecolors="none")
    ax.set_aspect("equal", adjustable="box")
    ax.set_title("V4.5.4 Demo: 80 mm FOV / 30 mm wafer / ~1 mm step")
    ax.set_xlabel("X (mm)"); ax.set_ylabel("Y (mm)")
    fig.colorbar(scatter, ax=ax, label="Thickness 1 (µm)")
    fig.savefig(PREVIEW_PATH, dpi=170)
    plt.close(fig)


if __name__ == "__main__":
    main()
