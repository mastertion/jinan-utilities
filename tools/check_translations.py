# -*- coding: utf-8 -*-
"""校验翻译文案不会触发 HA 前端的 ICU MessageFormat 解析错误.

背景：HA 前端用 intl-messageformat 编译每条 config flow 文案，任何裸
`{ ... }` 都会被当作参数占位符解析。文案里写 JSON 示例
（例如 {"access_token":"..."}）会抛 MALFORMED_ARGUMENT，界面直接显示
"Translation error: MALFORMED_ARGUMENT"，整个配置页无法使用。

规则：
  1. 所有 `{xxx}` 里的 xxx 必须是合法参数名（字母/下划线开头）；
  2. 花括号必须配对；
  3. ASCII 单引号在 ICU 里是转义符，描述文案中应避免使用。

用法：python tools/check_translations.py
本脚本只用标准库；如需完全复现前端行为，可额外用
`npm i intl-messageformat` 后对每条文案执行 new IntlMessageFormat(v, lang)。
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "jinan_utilities"
FILES = [
    ROOT / "strings.json",
    ROOT / "translations" / "zh-Hans.json",
    ROOT / "translations" / "zh-CN.json",
    ROOT / "translations" / "en.json",
]
VALID_ARG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 代码中实际会提供的占位符
KNOWN_ARGS = {"service", "error", "authorize_url", "captcha_url", "sms_status"}


def main() -> int:
    problems: list[str] = []
    checked = 0
    for file in FILES:
        data = json.loads(file.read_text(encoding="utf-8"))

        def walk(node, path=""):
            nonlocal checked
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}" if path else key)
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")
            elif isinstance(node, str):
                checked += 1
                for match in re.finditer(r"\{([^{}]*)\}", node):
                    name = match.group(1)
                    if not VALID_ARG.match(name):
                        problems.append(
                            f"{file.name} {path}: 非法占位符 {match.group(0)!r} "
                            f"(会导致 MALFORMED_ARGUMENT)"
                        )
                    elif name not in KNOWN_ARGS:
                        problems.append(f"{file.name} {path}: 代码中未提供参数 {{{name}}}")
                if node.count("{") != node.count("}"):
                    problems.append(f"{file.name} {path}: 花括号未配对")
                if "'" in node:
                    problems.append(
                        f"{file.name} {path}: 含 ASCII 单引号（ICU 转义符），请改用中文引号"
                    )

        walk(data)

    if problems:
        print(f"校验失败，共 {len(problems)} 处：")
        for item in problems:
            print("  -", item)
        return 1
    print(f"OK：{len(FILES)} 个文件、{checked} 条文案全部通过 ICU 占位符校验")
    return 0


if __name__ == "__main__":
    sys.exit(main())
