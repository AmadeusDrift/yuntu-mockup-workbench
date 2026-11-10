"""Versioned, source-and-scene-specific compatibility reports."""
import hashlib
import json

VERSION = 1


def key(template):
    value = {'version': VERSION, 'digest': template['digest'], 'scenes': [
        {name: scene.get(name) for name in ('id','mode','targets','artwork_roles')}
        for scene in template['scenes']]}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def report(template, issues, warnings=(), features=None):
    return {'version': VERSION, 'key': key(template),
            'status': 'blocked' if issues else 'review' if warnings else 'ready',
            'issues': issues, 'warnings': list(dict.fromkeys(warnings)), 'features': features or {}}


def current(template):
    value = template.get('compatibility')
    return value if value and value.get('key') == key(template) else None


def require(template):
    value = current(template)
    if value and value['status'] == 'blocked':
        messages = [issue['message'] for issue in value['issues']]
        raise ValueError(template.get('name','模板') + ' 暂不能处理，请调整以下图层后重新导入，或跳过对应场景：\n' + '\n'.join(messages))
