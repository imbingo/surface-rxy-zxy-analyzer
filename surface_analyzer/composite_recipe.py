"""Versioned, self-contained composite recipes and deterministic file-role discovery."""
from copy import deepcopy
from pathlib import Path
import math
import codecs

COMPOSITE_TYPE = 'SurfaceAnalyzerCompositeRecipe'
COMPOSITE_SCHEMA = 1
ROLE_LABELS = {'stack': '堆叠总成', 'base1': '单片 1', 'base2': '单片 2',
               'base': '基准面', 'measure': '测量面'}
DEFAULT_KEYWORDS = {'stack': '总成', 'base1': '单片1', 'base2': '单片2',
                    'base': '基准', 'measure': '测量'}


def required_roles(recipe):
    if recipe['module'] == 'parallelism':
        return ('base', 'measure')
    return ('stack', 'base1', 'base2') if recipe['include_base2'] else ('stack', 'base1')


def recipe_kind(data):
    if data.get('recipe_type') != COMPOSITE_TYPE:
        return '单层 Recipe'
    return '膜厚组合 Recipe' if data.get('module') == 'thickness' else '平行度组合 Recipe'


def validate_single_snapshot(data):
    from .recipe_library import validate_recipe
    if not isinstance(data, dict) or data.get('recipe_type') not in (None, 'SurfaceRxyZxyAnalyzerRecipe'):
        raise ValueError('槽内必须是单层 Recipe 参数副本')
    validate_recipe(data)
    for section in ('input', 'column_mapping', 'units', 'filter', 'large_file', 'roi'):
        if not isinstance(data.get(section), dict):
            raise ValueError(f'单层参数快照缺少 {section}，请重新写入数据槽')
    required_fields={'input':('layout_mode','search_start_row','encoding','delimiter','pitch_source'),
                     'filter':('mode_index','neighbor_k','threshold_um','sigma_k','sigma_iters','sigma_residual_order','gaussian_sigma_mm'),
                     'large_file':('auto_sample','sample_method','import_limit','threshold_mb','display_limit','matrix_analysis_threshold','grid_count','stride_n'),
                     'roi':('enabled','shapes')}
    for section,keys in required_fields.items():
        if any(key not in data[section] or data[section][key] is None for key in keys):
            raise ValueError(f'单层参数快照 {section} 不完整，请重新写入')
    if type(data['large_file']['auto_sample']) is not bool or type(data['roi']['enabled']) is not bool:
        raise ValueError('抽样与 ROI 开关必须为布尔值')
    if data['large_file']['sample_method'] not in ('file_position','spatial_grid','stride'):
        raise ValueError('未知分析抽样方式')
    if type(data['input']['search_start_row']) is not int or not 0 <= data['input']['search_start_row'] <= 10000000:
        raise ValueError('正文搜索起始行无效')
    if data['input']['encoding']!='auto':
        try:codecs.lookup(data['input']['encoding'])
        except (TypeError,LookupError):raise ValueError('未知导入编码') from None
    if data['input']['delimiter'] not in ('auto',',',';','；','\t','|','whitespace'):
        raise ValueError('未知分隔符')
    mapping = data['column_mapping']
    cols = [mapping.get(f'{axis}_col') for axis in 'xyz']
    if any(not isinstance(c, str) or not c for c in cols) or len(set(cols)) != 3:
        raise ValueError('单层 Recipe 的 X/Y/Z 映射必须为三个不同的非空列名')
    if any(data['units'].get(f'{axis}_unit') not in ('mm', 'µm', 'nm') for axis in 'xyz'):
        raise ValueError('单层 Recipe 单位无效')
    if data['input'].get('layout_mode') not in ('point_table', 'pixel_xy', 'height_matrix', 'zygo_xyz'):
        raise ValueError('单层 Recipe 导入类型无效')
    if any(action not in ('CW90','CCW90','ROT180','SWAP','FLIPX','FLIPY','ORIGIN(0,0)')
           for action in data.get('transform_pipeline', [])):
        raise ValueError('单层 Recipe 包含未知姿态变换')
    bounds = {'filter': {'mode_index': (0,4), 'neighbor_k': (3,200),
                         'threshold_um': (.01,10000), 'sigma_k': (1.,6.),
                         'sigma_iters': (1,20), 'gaussian_sigma_mm': (.0001,1000)},
              'large_file': {'import_limit': (10000,5000000), 'display_limit': (5000,1000000),
                             'threshold_mb': (1,4096), 'matrix_analysis_threshold': (10000,10000000),
                             'grid_count': (0,2000), 'stride_n': (1,1000000)}}
    integer_fields={'mode_index','neighbor_k','sigma_iters','import_limit','display_limit','threshold_mb','matrix_analysis_threshold','grid_count','stride_n'}
    for section, fields in bounds.items():
        for key,(lo,hi) in fields.items():
            value = data[section].get(key)
            if value is not None and key in integer_fields and type(value) is not int:
                raise ValueError(f'单层 Recipe {section}.{key} 必须为整数')
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float))
                                       or not math.isfinite(value) or not lo <= value <= hi):
                raise ValueError(f'单层 Recipe {section}.{key} 超出有效范围')
    from .rendering.settings import validate_display
    from .adjustment_panel import validate_fixture, default_fixture
    validate_display(data.get('display',{}))
    validate_fixture(data.get('adjustment_config',default_fixture()))
    if data['input'].get('pitch_source','auto') not in ('auto','manual'):
        raise ValueError('未知 Pitch 来源')
    for shape in data['roi'].get('shapes',[]):
        if shape.get('type','rect') not in ('rect','circle','smart_face'):
            raise ValueError('未知 ROI 类型')
        if shape.get('type','rect')=='rect':
            if any(not isinstance(shape.get(k),(int,float)) or not math.isfinite(shape[k]) or shape[k]<=0 for k in ('width','height')):
                raise ValueError('ROI 矩形尺寸必须为正有限数')
        if shape.get('view','XY') not in ('XY','XZ','YZ'):
            raise ValueError('未知 ROI 视图')
    return data


def validate_composite(data):
    if not isinstance(data, dict) or data.get('recipe_type') != COMPOSITE_TYPE:
        raise ValueError('不是组合 Recipe')
    if type(data.get('schema_version')) is not int or data['schema_version'] != COMPOSITE_SCHEMA:
        raise ValueError('不支持的组合 Recipe schema_version（当前为 1）')
    if data.get('module') not in ('parallelism','thickness'):
        raise ValueError('组合 Recipe 模块无效')
    if not isinstance(data.get('name'),str) or not data['name'].strip():
        raise ValueError('组合 Recipe 名称不能为空')
    if type(data.get('include_base2')) is not bool:
        raise ValueError('组合 Recipe 必须明确保存是否参与单片 2')
    if data['module']=='parallelism' and data['include_base2']:
        raise ValueError('平行度不能配置单片 2')
    roles = required_roles(data)
    if not isinstance(data.get('roles'),dict) or set(data['roles']) != set(roles):
        raise ValueError('组合 Recipe 角色缺失或包含多余角色')
    for role in roles:
        validate_single_snapshot(data['roles'][role])
    for section in ('matching','policy','export','registration'):
        if not isinstance(data.get(section),dict):raise ValueError(f'组合 Recipe {section} 必须为对象')
    if 'library_metadata' in data and not isinstance(data['library_metadata'],dict):
        raise ValueError('组合 Recipe library_metadata 必须为对象')
    matching = data.get('matching',{})
    if (matching.get('mode') != 'filename_contains' or matching.get('case_sensitive') is not False
            or not isinstance(matching.get('keywords'),dict) or set(matching['keywords']) != set(roles)):
        raise ValueError('角色规则必须为不区分大小写的文件名关键词匹配')
    if any(not isinstance(v,str) or not v.strip() for v in matching['keywords'].values()):
        raise ValueError('每个角色的匹配关键词不能为空')
    policy = data.get('policy',{})
    if policy.get('estimated') not in ('reject','allow_marked') or policy.get('deletion_mismatch') not in ('fail','skip_marked'):
        raise ValueError('必须明确设置估算质量与删点重放策略')
    export = data.get('export',{})
    if any(type(export.get(k)) is not bool for k in ('csv','report')):
        raise ValueError('必须明确设置 CSV / 报告导出选项')
    calculation=data.get('calculation',{})
    if not isinstance(calculation,dict) or (calculation.get('judgment') is not None and not isinstance(calculation['judgment'],dict)):
        raise ValueError('组合计算设置无效')
    for value in calculation.get('judgment',{}).values():
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0):
            raise ValueError('组合判定阈值必须为非负有限数或空值')
    registration = data.get('registration',{})
    if data['module']=='thickness':
        if registration.get('mode') not in ('none','fixed','auto'):
            raise ValueError('未知配准方式')
        tolerance = registration.get('tolerance_mm')
        if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or not .001 <= tolerance <= 10.:
            raise ValueError('配准容差必须为正有限数')
        offsets = registration.get('offsets',{})
        if not isinstance(offsets,dict) or set(offsets) != set(roles)-{'stack'}:
            raise ValueError('配准偏移角色不完整')
        for pair in offsets.values():
            if (not isinstance(pair,list) or len(pair)!=2 or any(isinstance(v,bool)
                    or not isinstance(v,(int,float)) or not math.isfinite(v) for v in pair)):
                raise ValueError('XY 偏移必须为两个有限数（mm）')
    elif registration:
        raise ValueError('平行度组合 Recipe 不使用 XY 配准')
    return data


def build_composite(module, records, name, *, include_base2=False, registration=None,
                    keywords=None, policy=None, export=None, app_version=''):
    result = dict(recipe_type=COMPOSITE_TYPE, schema_version=COMPOSITE_SCHEMA, module=module,
                  name=name.strip(), include_base2=bool(include_base2) if module=='thickness' else False,
                  app_version=app_version)
    roles = required_roles(result)
    missing = [ROLE_LABELS[r] for r in roles if not records.get(r) or not records[r].get('single_recipe')]
    if missing:
        raise ValueError('缺少数据或写入时单层参数快照：'+'、'.join(missing)+'；请重新写入对应槽')
    result.update(roles={r:deepcopy(records[r]['single_recipe']) for r in roles},
                  registration=deepcopy(registration or {}),
                  matching=dict(mode='filename_contains',case_sensitive=False,
                                keywords={r:(keywords or DEFAULT_KEYWORDS)[r].strip() for r in roles}),
                  policy=deepcopy(policy or dict(estimated='reject',deletion_mismatch='fail')),
                  export=deepcopy(export or dict(csv=True,report=True)))
    # No source path or point/result data is included. Deletion hashes remain
    # compatibility evidence, never an instruction to reuse old point indices.
    for single in result['roles'].values():
        single.pop('library_metadata',None)
        single.get('manual_deletion',{}).pop('source_name',None)
    return validate_composite(result)


def supported_suffixes():
    from .format_extensions import FormatRegistry
    from .mixins.data_io import DataIOMixin
    suffixes=set(DataIOMixin.TEXT_SUFFIXES+DataIOMixin.EXCEL_SUFFIXES)
    for entry in FormatRegistry().entries():
        if entry.get('status') == 'ready':
            suffixes.update(entry.get('suffixes',[]))
    return {s.casefold() for s in suffixes}


def validate_group(group, recipe, suffixes=None):
    suffixes=supported_suffixes() if suffixes is None else suffixes
    roles=required_roles(recipe); errors=[]; files=group.get('files',{}); used={}
    if 'sample_id' in group and (not isinstance(group['sample_id'],str) or not group['sample_id'].strip()):
        errors.append('样品编号为空或无效')
    for role in roles:
        value=files.get(role)
        candidates = value if isinstance(value,list) else ([value] if value else [])
        if not candidates:errors.append(f'缺失：{ROLE_LABELS[role]}');continue
        if len(candidates)!=1:errors.append(f'歧义：{ROLE_LABELS[role]} ({len(candidates)})');continue
        path=Path(candidates[0]).expanduser().resolve()
        if not path.is_file():errors.append(f'缺失：{ROLE_LABELS[role]}文件不存在')
        elif path.suffix.casefold() not in suffixes:errors.append(f'不支持：{path.name}')
        # Resolve symlinks and Windows case aliases; physical file reuse is forbidden.
        key=str(path).casefold()
        if key in used:errors.append(f'冲突：{ROLE_LABELS[used[key]]} / {ROLE_LABELS[role]} 共用文件')
        used[key]=role
    return errors


def scan_groups(root, recipe, suffixes=None, check_cancel=lambda:None):
    validate_composite(recipe)
    suffixes=supported_suffixes() if suffixes is None else suffixes
    groups=[];folders=[]
    for candidate in Path(root).iterdir():
        check_cancel()
        if candidate.is_dir():folders.append(candidate)
    for folder in sorted(folders,key=lambda p:p.name.casefold()):
        check_cancel()
        files=[]
        for p in folder.iterdir():
            check_cancel()
            if p.is_file() and p.suffix.casefold() in suffixes:files.append(p)
        matches={r:[str(p.resolve()) for p in sorted(files) if recipe['matching']['keywords'][r].casefold() in p.name.casefold()]
                 for r in required_roles(recipe)}
        group=dict(sample_id=folder.name,files=matches,selected=True)
        group['errors']=validate_group(group,recipe,suffixes);groups.append(group)
    return groups
