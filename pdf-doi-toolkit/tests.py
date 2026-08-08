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
from pdf_doi_toolkit.fuzzy import FilenameParser, FilenameParseResult
from pdf_doi_toolkit.matcher import DOIMatcher
from pdf_doi_toolkit.config import AUTHOR_MATCH_CHAR_THRESHOLD
from pdf_doi_toolkit.cache import DOICache
from pdf_doi_toolkit.title_extractor import TitleExtractor
from pdf_doi_toolkit.main import build_parser


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


def test_filename_parser():
    print("=== 测试文件名解析 ===")

    # 标准模式: Author_Year_Keyword
    r = FilenameParser.parse("Smith_2023_Quantum.pdf")
    assert r.author == "Smith", f"Expected Smith, got {r.author}"
    assert r.year == 2023, f"Expected 2023, got {r.year}"
    assert "Quantum" in r.keywords, f"Quantum not in keywords: {r.keywords}"
    print("  ✅ Smith_2023_Quantum.pdf")

    # 只有作者+年份
    r = FilenameParser.parse("Smith_2023.pdf")
    assert r.author == "Smith"
    assert r.year == 2023
    assert r.keywords == []
    print("  ✅ Smith_2023.pdf")

    # 年份前置
    r = FilenameParser.parse("2023_Smith_Quantum.pdf")
    assert r.author == "Smith", f"Expected Smith, got {r.author}"
    assert r.year == 2023
    print("  ✅ 2023_Smith_Quantum.pdf")

    # 多词关键词
    r = FilenameParser.parse("Zhang_2023_Deep_Learning.pdf")
    assert r.author == "Zhang"
    assert r.year == 2023
    assert "Deep" in r.keywords
    assert "Learning" in r.keywords
    print("  ✅ Zhang_2023_Deep_Learning.pdf")

    # 连字符分隔
    r = FilenameParser.parse("Smith-2023-Quantum.pdf")
    assert r.author == "Smith"
    assert r.year == 2023
    print("  ✅ Smith-2023-Quantum.pdf")

    # 无年份
    r = FilenameParser.parse("Smith_Quantum.pdf")
    assert r.author == "Smith"
    assert r.year is None
    print("  ✅ Smith_Quantum.pdf (no year)")

    # 无作者（裸文件名）
    r = FilenameParser.parse("paper.pdf")
    assert r.author == ""
    assert r.year is None
    print("  ✅ paper.pdf (bare)")

    # 期刊名模式: Author_Journal_Year
    r = FilenameParser.parse("Smith_Nature_2023.pdf")
    assert r.author == "Smith", f"Expected Smith, got {r.author}"
    assert r.year == 2023
    print("  ✅ Smith_Nature_2023.pdf")

    # 连字符作者名（不分裂）
    r = FilenameParser.parse("Gonzalez-Cabaleiro_2023_Biofilm.pdf")
    assert r.author == "Gonzalez-Cabaleiro", f"Expected Gonzalez-Cabaleiro, got {r.author}"
    assert r.year == 2023
    assert "Biofilm" in r.keywords
    print("  ✅ Gonzalez-Cabaleiro_2023_Biofilm.pdf")

    # 年份后置 + 期刊
    r = FilenameParser.parse("Wang_Advanced_Materials_2023.pdf")
    assert r.author == "Wang"
    assert r.year == 2023
    print("  ✅ Wang_Advanced_Materials_2023.pdf")

    # 中文姓氏
    r = FilenameParser.parse("Zhang_2023.pdf")
    assert r.author == "Zhang"
    assert r.year == 2023
    print("  ✅ Zhang_2023.pdf")

    # 作者=关键词去重
    r = FilenameParser.parse("Smith_2023_Smith.pdf")
    assert r.author == "Smith"
    assert r.year == 2023
    # "Smith" 应当被去重，不在 keywords 中
    assert "Smith" not in r.keywords, f"Smith should be deduped from keywords: {r.keywords}"
    print("  ✅ Smith_2023_Smith.pdf (author-keyword dedup)")

    # 无后缀
    r = FilenameParser.parse("Smith_2023_Quantum")
    assert r.author == "Smith"
    assert r.year == 2023
    print("  ✅ Smith_2023_Quantum (no extension)")

    # 空字符串
    r = FilenameParser.parse("")
    assert r.author == ""
    assert r.year is None
    assert r.keywords == []
    print("  ✅ empty string")

    print()


def test_fuzzy_score():
    print("=== 测试模糊匹配评分 ===")
    matcher = DOIMatcher(".")

    # 高置信度: 作者+年份+关键词全部匹配
    parsed = FilenameParseResult(author="Smith", year=2023, keywords=["Quantum"])
    cr = {"author": "John Smith", "title": "Quantum Computing Advances", "year": "2023"}
    score = matcher._compute_fuzzy_score(parsed, cr)
    assert score >= 90, f"Expected >= 90, got {score}"
    print(f"  ✅ 高置信度: {score}")

    # 中置信度: 作者+关键词，无年份
    parsed2 = FilenameParseResult(author="Smith", year=None, keywords=["Quantum"])
    score2 = matcher._compute_fuzzy_score(parsed2, cr)
    assert 50 <= score2 < 90, f"Expected 50-90, got {score2}"
    print(f"  ✅ 中置信度: {score2}")

    # 低置信度: 仅关键词，不匹配
    parsed3 = FilenameParseResult(author="", year=None, keywords=["Nothing"])
    cr3 = {"author": "John Smith", "title": "Quantum Computing", "year": "2023"}
    score3 = matcher._compute_fuzzy_score(parsed3, cr3)
    assert score3 < 40, f"Expected < 40, got {score3}"
    print(f"  ✅ 低置信度: {score3}")

    # 仅年份匹配
    parsed4 = FilenameParseResult(author="", year=2023, keywords=[])
    cr4 = {"author": "Someone Else", "title": "Unrelated", "year": "2023"}
    score4 = matcher._compute_fuzzy_score(parsed4, cr4)
    assert score4 == 30, f"Expected 30 (year only), got {score4}"
    print(f"  ✅ 仅年份匹配: {score4}")

    # 年份邻近但不精确
    parsed5 = FilenameParseResult(author="", year=2022, keywords=[])
    cr5 = {"author": "Someone Else", "title": "Unrelated", "year": "2023"}
    score5 = matcher._compute_fuzzy_score(parsed5, cr5)
    assert score5 == 20, f"Expected 20 (year near), got {score5}"
    print(f"  ✅ 年份邻近匹配: {score5}")

    print()


def test_fuzzy_integration():
    print("=== 测试模糊匹配集成 ===")
    # 空 Cat C 处理
    matcher = DOIMatcher(".")
    matcher.entries = []
    matcher._cat_a = []
    matcher._cat_b = []
    matcher._cat_c = []
    matcher.results = []
    result = matcher.run_fuzzy_fallback()
    assert result == []
    print("  ✅ 空 Cat C 处理正常")

    # Cat C 仅含无信息文件名
    matcher2 = DOIMatcher(".")
    matcher2.entries = [{"pdf_name": "paper.pdf", "json_title": "", "json_author": ""}]
    matcher2._cat_a = []
    matcher2._cat_b = []
    matcher2._cat_c = [{"pdf_name": "paper.pdf", "json_title": "", "json_author": ""}]
    matcher2.results = []
    result2 = matcher2.run_fuzzy_fallback()
    assert len(result2) == 1
    assert result2[0]["note"] == "fuzzy: insufficient_clues"
    print("  ✅ 无信息文件名处理正常")

    print()

def test_title_extractor():
    print("=== 测试标题提取器 ===")

    # 从 Markdown 提取标题
    md = """# Quantum Computing

Received: 12 January 2024
"""
    # 第一行是 markdown 标题标记，跳过；第二行空；第三行含 "received" 特征词被过滤
    title = TitleExtractor._extract_title_from_md(md)
    assert title is None, f"应返回 None (所有前置行被过滤), got {title!r}"
    print("  ✅ 标题被过滤")

    md2 = """Quantum Computing Advances in 2024

## Abstract
This paper discusses quantum computing.
"""
    title2 = TitleExtractor._extract_title_from_md(md2)
    assert title2 == "Quantum Computing Advances in 2024", f"Got {title2!r}"
    print("  ✅ 正常标题提取")

    md3 = """![](images/p0_0.jpg)

Quantum Computing Advances
"""
    title3 = TitleExtractor._extract_title_from_md(md3)
    assert title3 == "Quantum Computing Advances", f"Got {title3!r}"
    print("  ✅ 图片引用过滤")

    # 空输入
    assert TitleExtractor._extract_title_from_md("") is None
    assert TitleExtractor._extract_title_from_md(None) is None
    print("  ✅ 空输入处理")

    # 定位 OCR 目录（不应崩溃）
    ex = TitleExtractor()
    ocr_dir = ex._locate_ocr_dir()
    # 仓库根下有 DeepSeek-OCR 目录，应能找到
    assert ocr_dir is not None, "应能自动定位 DeepSeek-OCR 目录"
    print(f"  ✅ 自动定位 OCR 目录: {ocr_dir}")

    print()


def test_cache():
    print("=== 测试缓存 ===")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cache_path = os.path.join(tmp, "cache.json")

        # 写入
        cache = DOICache(cache_path)
        cache.load()
        cache.set("Smith_2023.pdf", {
            "doi": "10.1000/quantum.2023.001",
            "title": "Quantum Computing",
            "author": "John Smith",
            "note": "ok",
            "match": True,
        })
        cache.save()
        assert os.path.exists(cache_path), "缓存文件应写入磁盘"
        print("  ✅ 写入缓存")

        # 重新加载
        cache2 = DOICache(cache_path)
        entries = cache2.load()
        assert "Smith_2023.pdf" in entries, "重新加载应包含条目"
        entry = cache2.get("Smith_2023.pdf")
        assert entry["doi"] == "10.1000/quantum.2023.001"
        assert entry["match"] is True
        print("  ✅ 读回缓存")

        # 不存在的键
        assert cache2.get("missing.pdf") is None
        print("  ✅ 缺失键返回 None")

        # 版本不匹配
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write('{"version": 999, "entries": {"x.pdf": {"doi": "10.x"}}}')
        cache3 = DOICache(cache_path)
        entries3 = cache3.load()
        assert entries3 == {}, "版本不匹配应丢弃缓存"
        print("  ✅ 版本不匹配丢弃")

        # 损坏文件
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        cache4 = DOICache(cache_path)
        entries4 = cache4.load()
        assert entries4 == {}, "损坏文件应清空"
        print("  ✅ 损坏文件清空")

        # 无路径缓存（不持久化）
        cache5 = DOICache(None)
        cache5.set("a.pdf", {"doi": "10.x"})
        assert "a.pdf" in cache5
        assert cache5.save() is False, "无路径不应保存"
        print("  ✅ 无路径缓存不持久化")

    print()


def test_rename_conflicts():
    print("=== 测试重命名冲突检测 ===")
    matcher = DOIMatcher(".")  # 不实际重命名，只测 _get_conflicts

    matcher.results = [
        {"pdf_name": "a.pdf", "cr_doi": "10.1000/duplicate", "match": True},
        {"pdf_name": "b.pdf", "cr_doi": "10.1000/duplicate", "match": True},
        {"pdf_name": "c.pdf", "cr_doi": "10.1000/unique", "match": True},
    ]

    conflicts = matcher._get_conflicts()
    assert len(conflicts) == 1, f"应检测到 1 组冲突, got {len(conflicts)}"
    assert conflicts[0]["doi"] == "10.1000/duplicate"
    assert set(conflicts[0]["pdfs"]) == {"a.pdf", "b.pdf"}
    print("  ✅ 冲突检测: 多 PDF 同 DOI")

    # 无冲突
    matcher.results = [
        {"pdf_name": "a.pdf", "cr_doi": "10.1000/one", "match": True},
        {"pdf_name": "b.pdf", "cr_doi": "10.1000/two", "match": True},
    ]
    assert matcher._get_conflicts() == []
    print("  ✅ 无冲突场景")

    # 空结果
    matcher.results = []
    assert matcher._get_conflicts() == []
    print("  ✅ 空结果")

    print()


def test_cli_parser():
    print("=== 测试 CLI 参数解析 ===")
    parser = build_parser()

    # 必需参数
    args = parser.parse_args(["--dir", "test_pdfs"])
    assert args.dir == "test_pdfs"
    assert args.fuzzy is False
    assert args.ocr is False
    assert args.no_cache is False
    print("  ✅ 基本参数")

    # 全部参数
    args2 = parser.parse_args([
        "--dir", "test_pdfs",
        "--fuzzy", "--ocr",
        "--ocr-dir", "../DeepSeek-OCR",
        "--no-cache",
        "--cache-file", "my_cache.json",
        "--checklist",
        "--apply-manual", "manual.json",
        "--no-rename",
        "--report", "report.md",
    ])
    assert args2.fuzzy is True
    assert args2.ocr is True
    assert args2.ocr_dir == "../DeepSeek-OCR"
    assert args2.no_cache is True
    assert args2.cache_file == "my_cache.json"
    assert args2.checklist is True
    assert args2.apply_manual == "manual.json"
    assert args2.no_rename is True
    assert args2.report == "report.md"
    print("  ✅ 全部参数")

    # 缺少 --dir 应报错
    try:
        parser.parse_args([])
        assert False, "缺少 --dir 应报错"
    except SystemExit:
        pass
    print("  ✅ 缺少 --dir 报错")

    print()


if __name__ == "__main__":
    test_utils()
    test_authors_match()
    test_sciencedirect()
    test_xmol()
    test_config()
    test_filename_parser()
    test_fuzzy_score()
    test_fuzzy_integration()
    test_title_extractor()
    test_cache()
    test_rename_conflicts()
    test_cli_parser()
    print("所有测试通过！")