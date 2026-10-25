"""Independent implementation of the documented Sensofar PLUX height layout.

No third-party reader code is embedded. API v1 returns physical millimetres.
"""
import xml.etree.ElementTree as ET
import zipfile

import numpy as np


def read(path, context):
    context.report(10, '正在检查 PLUX 容器')
    with zipfile.ZipFile(path) as archive:
        members = {}
        for entry in archive.infolist():
            context.check_cancel()
            name = entry.filename.replace('\\', '/').removeprefix('./').casefold()
            if name in members:
                raise ValueError('PLUX 包含重复文件名')
            members[name] = entry
        index = members.get('index.xml')
        if index is None or index.file_size > 8 * 1024 * 1024:
            raise ValueError('PLUX index.xml 缺失或过大')
        xml_bytes = archive.read(index)
        if b'<!DOCTYPE' in xml_bytes.upper() or b'<!ENTITY' in xml_bytes.upper():
            raise ValueError('PLUX XML 不支持外部实体或 DTD')
        root = ET.fromstring(xml_bytes)
        for element in root.iter():
            element.tag = element.tag.split('}')[-1]
        general = root.find('GENERAL')
        if general is None:
            raise ValueError('PLUX 缺少 GENERAL 元数据')
        width = int(general.findtext('IMAGE_SIZE_X', '0'))
        height = int(general.findtext('IMAGE_SIZE_Y', '0'))
        if width < 1 or height < 1 or width * height > 250_000_000:
            raise ValueError('PLUX 矩阵尺寸不受支持')
        pitch_x = float(general.findtext('FOV_X', 'nan')) * .001
        pitch_y = float(general.findtext('FOV_Y', 'nan')) * .001
        if not np.isfinite([pitch_x, pitch_y]).all() or min(pitch_x, pitch_y) <= 0:
            raise ValueError('PLUX 物理采样间距无效')
        layers = [e for e in root if e.tag.startswith('LAYER_') and e.findtext('FILENAME_Z')]
        if context.layer_id is not None:
            layers = [e for e in layers if e.tag == context.layer_id]
        if not layers:
            raise ValueError('PLUX 没有可读取的高度层')
        if len(layers) > 1:
            raise ValueError('PLUX 包含多个高度层，请先在原厂软件导出所需单层；当前扩展不自动猜选')
        layer = layers[0]
        filename = layer.findtext('FILENAME_Z').replace('\\', '/').removeprefix('./')
        member = members.get(filename.casefold())
        expected = width * height * 4
        if member is None or member.file_size != expected:
            raise ValueError('PLUX 高度层字节数与矩阵尺寸不一致')
        context.report(25, '正在读取 PLUX 高度矩阵')
        data = bytearray(expected)
        view = memoryview(data)
        with archive.open(member) as stream:
            offset = 0
            while offset < expected:
                context.check_cancel()
                block = stream.read(min(1024 * 1024, expected - offset))
                if not block:
                    raise ValueError('PLUX 高度层意外截断')
                view[offset:offset + len(block)] = block
                offset += len(block)
                context.report(25 + int(45 * offset / expected), '正在解析 PLUX 高度')
        z = np.frombuffer(data, dtype='<f4').reshape(height, width).astype(np.float64)
        z *= .001
        z[~np.isfinite(z)] = np.nan
        metadata = {e.tag: e.text for e in general if e.text}
        metadata['layer_position_um'] = {
            axis: layer.findtext('POSITION_' + axis) for axis in 'XYZ'}
        return {
            'kind': 'height_matrix', 'units': 'mm', 'z': z,
            'pitch_x_mm': pitch_x, 'pitch_y_mm': pitch_y,
            'origin_xy_mm': [0., 0.],
            'metadata': metadata,
            'layer_id': layer.tag,
            'notes': '局部像素坐标；FOV_X/Y 按 µm/pixel；Z 按 µm。层台面位置仅保留为元数据，未叠加。',
        }
