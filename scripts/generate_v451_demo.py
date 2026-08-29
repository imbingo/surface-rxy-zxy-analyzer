"""Generate the deterministic V4.5.1 overlapping-layer Smart ROI demo."""

from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / 'demo_data'


def main():
    rows, cols = 60, 80
    rr, cc = np.mgrid[0:rows, 0:cols]
    x = cc.ravel() * 0.08
    y = rr.ravel() * 0.09
    xc, yc = np.mean(x), np.mean(y)
    lower = 0.180 + 0.00003 * x - 0.00002 * y
    upper = 0.240 + 0.00035 * (x - xc) ** 2 + 0.00022 * (y - yc) ** 2
    step = cc.ravel() >= 66
    upper[step] += 0.050
    hole = ((cc.ravel() >= 34) & (cc.ravel() <= 43)
            & (rr.ravel() >= 25) & (rr.ravel() <= 34))
    lower_frame = pd.DataFrame({
        'X_mm': x, 'Y_mm': y, 'Z_mm': lower, 'Layer': 'lower',
        '_matrix_row': rr.ravel(), '_matrix_col': cc.ravel()})
    upper_frame = pd.DataFrame({
        'X_mm': x[~hole], 'Y_mm': y[~hole], 'Z_mm': upper[~hole], 'Layer': 'upper',
        '_matrix_row': rr.ravel()[~hole], '_matrix_col': cc.ravel()[~hole]})
    output = DEMO / 'V4.5.1_重叠双层_Bow孔洞台阶_Demo.csv'
    pd.concat([lower_frame, upper_frame], ignore_index=True).to_csv(output, index=False)
    readme = DEMO / 'V4.5.1_SmartROI_Demo说明.md'
    readme.write_text(
        '# V4.5.1 Smart ROI 多视图 Demo\n\n'
        '- 文件包含 XY 完全重叠的上下两层；请映射 `X_mm/Y_mm/Z_mm`，单位均为 mm。\n'
        '- 在 XZ 或 YZ 点击约 0.18 mm 的下层，应只抓下层。\n'
        '- 点击约 0.24 mm 的上层，应跟随连续 Bow，中央孔洞保持为空。\n'
        '- 上层右侧有 0.05 mm 台阶，标准模式不应跨越。\n'
        '- 可先在 XZ/YZ 使用“框定候选范围”，再关闭按钮并点击种子。\n',
        encoding='utf-8')
    print(output)


if __name__ == '__main__':
    main()
