"""
pdf-doi-toolkit 单元测试
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_doi_toolkit.utils import (
    normalize_author, authors_match, doi_safe,
    strip_supplement, is_doi_pdf, is_sciencedirect,
)
from pdf_doi_toolkit.sciencedirect import ScienceDirectHandler
from pdf_doi_toolkit.xmol import XMolFallback
from pdf_doi_toolkit.config import AUTHOR_MATCH_CHAR_THRESHOLD


def test_utils():
    print("=== 测试工具函数 ===")

    # DOI 安全
    assert doi_safe("10.1016/j.bios.2024.117036") == "10.1016_j.bios.2024.117036"
    print("  ✅ doi_safe")

    # 剥离补充材料后缀
    assert strip_supplement("10.1021/acs.analchem.5c01686.s001") == "10.1021/acs.analchem.5c01686"
    assert strip_supplement("10.1021/acssensors.4c02007.s002") == "10.1021/acssensors.4c02007"
    assert strip_supplement("10.1021/acsnano.0c05010.s004") == "10.1021/acsnano.0c05010"
    assert strip_supplement("10.1016/j.bios.2024.117036") == "10.1016/j.bios.2024.117036"
    print("  ✅ strip_supplement")

    # 判断 DOI 格式
    assert is_doi_pdf("10.1021_acs_analchem_2c01450.pdf") == True
    assert is_doi_pdf("10.1002_smll.202507530.pdf") == True
    assert is_doi_pdf("9.pdf") == False
    assert is_doi_pdf("1-s2.0-S0956566324010431-main.pdf") == False
    print("  ✅ is_doi_pdf")

    # 判断 ScienceDirect
    assert is_sciencedirect("1-s2.0-S0956566324010431-main.pdf") == True
    assert is_sciencedirect("10.1016_j.bios.2024.117036.pdf") == False
    print("  ✅ is_sciencedirect")

    print()


def test_authors_match():
    print("=== 测试作者匹配 ===")

    # unicode 差异
    assert authors_match("Kutalek", "Kutálek") == True
    print("  ✅ unicode 差异: Kutalek vs Kutálek")

    # 拼写差异
    assert authors_match("Kalahdaran", "Kaladharan") == True
    print("  ✅ 拼写差异: Kalahdaran vs Kaladharan")

    # 连字符/空格差异
    assert authors_match("Lara González-Cabaleiro", "Lara González‐Cabaleiro") == True
    print("  ✅ 连字符差异: González-Cabaleiro vs González‐Cabaleiro")

    # 完全匹配
    assert authors_match("Yi Yang", "Yi Yang") == True
    print("  ✅ 完全匹配: Yi Yang vs Yi Yang")

    # 空格差异
    assert authors_match("Jae- Eul Shim", "Jae-Eul Shim") == True
    print("  ✅ 空格差异: Jae- Eul Shim vs Jae-Eul Shim")

    # 姓氏相同（中间名省略）
    assert authors_match("Seyed Mohammad Taghi Gharibzadeh", "Seyed Mohammad Taghi Ghar") == True
    print("  ✅ 姓氏相同: Gharibzadeh vs Ghar")

    # 完全不相关
    assert authors_match("Wei Zhou", "Wonil Nam") == False
    print("  ✅ 不相关: Wei Zhou vs Wonil Nam")

    # 正常匹配
    assert authors_match("Peng Zheng", "Peng Zheng") == True
    print("  ✅ 正常匹配: Peng Zheng vs Peng Zheng")

    # 边界: 空值
    assert authors_match("", "Test") == False
    assert authors_match("Test", "") == False
    print("  ✅ 空值处理")

    # 首字母+姓氏
    assert authors_match("Xiang-Yu Meng", "Xiangyu Meng") == True
    print("  ✅ 名称变体: Xiang-Yu Meng vs Xiangyu Meng")

    print()


def test_sciencedirect():
    print("=== 测试 ScienceDirect ===")
    sd = ScienceDirectHandler()

    pii = sd.extract_pii("1-s2.0-S0956566324010431-main.pdf")
    assert pii == "S0956566324010431"
    print(f"  ✅ extract_pii: {pii}")

    info = sd.parse_pii(pii)
    assert info["issn"] == "09565663"
    assert info["year"] == "2024"
    assert info["seq"] == "010431"
    print(f"  ✅ parse_pii: ISSN={info['issn']} 年份={info['year']} 序号={info['seq']}")

    desc = sd.describe("1-s2.0-S0956566324010431-main.pdf")
    assert "09565663" in desc
    assert "2024" in desc
    print(f"  ✅ describe: {desc}")

    # 非 ScienceDirect
    assert sd.extract_pii("random.pdf") == ""
    print("  ✅ 非 SD 文件返回空")

    print()


def test_xmol():
    print("=== 测试 x-mol ===")
    url = XMolFallback.search_url("Accumulation cross-over")
    assert "x-mol.net" in url
    assert "Accumulation" in url
    print(f"  ✅ search_url: {url}")

    entries = [
        {"pdf_name": "test.pdf", "final_title": "Test Title", "cr_doi": "10.xxxx/yyyyy"},
    ]
    checklist = XMolFallback.generate_checklist(entries)
    assert "test.pdf" in checklist
    assert "10.xxxx/yyyyy" in checklist
    assert "x-mol.net" in checklist
    assert "Test Title" in checklist
    print("  ✅ generate_checklist 包含所有必要字段")

    print()


def test_config():
    print("=== 测试配置 ===")
    assert AUTHOR_MATCH_CHAR_THRESHOLD == 0.7
    print(f"  ✅ AUTHOR_MATCH_CHAR_THRESHOLD = {AUTHOR_MATCH_CHAR_THRESHOLD}")
    print()


if __name__ == "__main__":
    test_utils()
    test_authors_match()
    test_sciencedirect()
    test_xmol()
    test_config()
    print("🎉 所有测试通过！")