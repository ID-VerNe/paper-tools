"""
模糊文件名匹配 — 大规模验证脚本

用大量真实风格的文件名模式测试 FilenameParser 的解析准确性。
不依赖外部 API，所有测试用例都有预期解析结果。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pdf_doi_toolkit.fuzzy import FilenameParser, FilenameParseResult
from pdf_doi_toolkit.matcher import DOIMatcher
from pdf_doi_toolkit.config import (
    FUZZY_AUTO_RENAME_THRESHOLD,
    FUZZY_REVIEW_THRESHOLD,
)
from pdf_doi_toolkit.xmol import XMolFallback

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}  {detail}")


def test_parser_variants():
    """测试各种真实风格的文件名模式"""
    global passed, failed
    cases = [
        # (文件名, 预期作者, 预期年份, 预期关键词, 说明)

        # === 标准模式: Author_Year_Keyword ===
        ("Smith_2023_Quantum.pdf", "Smith", 2023, ["Quantum"],
         "标准: 作者_年份_关键词"),
        ("Chen_2022_Graphene.pdf", "Chen", 2022, ["Graphene"],
         "中文姓氏"),
        ("Kim_2021_MOF.pdf", "Kim", 2021, ["MOF"],
         "韩文姓氏+缩写关键词"),
        ("Garcia_2020_Catalysis.pdf", "Garcia", 2020, ["Catalysis"],
         "西班牙姓氏"),

        # === 只有作者+年份 ===
        ("Smith_2023.pdf", "Smith", 2023, [],
         "仅有作者+年份"),
        ("Wang_2022.pdf", "Wang", 2022, [],
         "中文姓氏+年份"),
        ("Muller_2021.pdf", "Muller", 2021, [],
         "Unicode 姓氏"),

        # === 年份前置 ===
        ("2023_Smith_Quantum.pdf", "Smith", 2023, ["Quantum"],
         "年份前置"),
        ("2022_Wang_Nature.pdf", "Wang", 2022, ["Nature"],
         "年份前置+期刊名"),
        ("2021_Chen_ACS_Applied.pdf", "Chen", 2021, ["ACS", "Applied"],
         "年份前置+多词期刊"),

        # === 作者+期刊+年份 ===
        ("Smith_Nature_2023.pdf", "Smith", 2023, ["Nature"],
         "作者_期刊_年份"),
        ("Wang_Advanced_Materials_2023.pdf", "Wang", 2023, ["Advanced", "Materials"],
         "作者_多词期刊_年份"),
        ("Zhang_Angew_Chem_2022.pdf", "Zhang", 2022, ["Angew", "Chem"],
         "作者_期刊缩写_年份"),

        # === 连字符分隔 ===
        ("Smith-2023-Quantum.pdf", "Smith", 2023, ["Quantum"],
         "连字符分隔"),
        ("Wang-2022-Graphene.pdf", "Wang", 2022, ["Graphene"],
         "连字符+中文姓"),
        ("Li-2021.pdf", "Li", 2021, [],
         "连字符+仅作者年份"),

        # === 多词关键词 ===
        ("Zhang_2023_Deep_Learning.pdf", "Zhang", 2023, ["Deep", "Learning"],
         "多词关键词"),
        ("Wang_2023_Transfer_Learning.pdf", "Wang", 2023, ["Transfer", "Learning"],
         "多词关键词2"),
        ("Chen_2022_Single_Cell_RNA.pdf", "Chen", 2022, ["Single", "Cell", "RNA"],
         "多词关键词3"),

        # === 无年份 ===
        ("Smith_Quantum.pdf", "Smith", None, ["Quantum"],
         "仅有作者+关键词"),
        ("Wang_Nature.pdf", "Wang", None, ["Nature"],
         "作者+期刊名"),

        # === 无作者 ===
        ("2023_Quantum.pdf", "", 2023, ["Quantum"],
         "仅有年份+关键词"),
        ("2022_Graphene.pdf", "", 2022, ["Graphene"],
         "年份+关键词"),

        # === 裸文件名 ===
        ("paper.pdf", "", None, [],
         "单关键词"),
        ("manuscript.pdf", "", None, [],
         "常见词"),
        ("thesis.pdf", "", None, [],
         "thesis"),
        ("main.pdf", "", None, [],
         "main"),

        # === 连字符作者名 ===
        ("Gonzalez-Cabaleiro_2023_Biofilm.pdf", "Gonzalez-Cabaleiro", 2023, ["Biofilm"],
         "连字符作者(不分裂)"),
        ("Lopez-Urrutia_2022_Plankton.pdf", "Lopez-Urrutia", 2022, ["Plankton"],
         "连字符作者2"),

        # === 作者=关键词去重 ===
        ("Smith_2023_Smith.pdf", "Smith", 2023, [],
         "作者名同时出现在关键词中应去重"),
        ("Wang_2022_Wang_Research.pdf", "Wang", 2022, [],
         "作者名部分去重, Research 是停用词"),

        # === Unicode ===
        ("Kutalek_2023_Polymer.pdf", "Kutalek", 2023, ["Polymer"],
         "Unicode 重音字符"),
        ("Gonzalez_2022_Chemistry.pdf", "Gonzalez", 2022, ["Chemistry"],
         "Unicode 重音字符2"),
        ("Muller_2021_Physics.pdf", "Muller", 2021, ["Physics"],
         "Unicode umlaut"),

        # === 年份边界 ===
        ("Smith_1999_Old.pdf", "Smith", 1999, ["Old"],
         "年份边界: 1900-2099 下限附近"),
        ("Smith_2099_Future.pdf", "Smith", 2099, ["Future"],
         "年份边界: 上限附近"),
        # 1900 以下不应识别为年份
        ("Smith_1899_Ancient.pdf", "Smith", None, ["Ancient"],
         "低于1900不是年份"),

        # === 数字关键词 ===
        ("Smith_2023_3D_Printing.pdf", "Smith", 2023, ["3D", "Printing"],
         "数字开头关键词"),

        # === 空串/边界 ===
        ("", "", None, [],
         "空字符串"),
        (".pdf", "", None, [],
         "仅有后缀"),
        ("Smith_2023_", "Smith", 2023, [],
         "尾部下划线"),
        ("_2023_Smith.pdf", "", 2023, ["Smith"],
         "前导下划线, 年份后仅一段, 保守策略不视为作者, 作为关键词保留"),

        # === 混合分隔符 ===
        ("Smith_2023-Nature.pdf", "Smith", 2023, ["Nature"],
         "混合下划线+连字符(数字附近分裂)"),
        ("Smith-2023_Quantum.pdf", "Smith", 2023, ["Quantum"],
         "混合连字符+下划线(数字附近分裂)"),

        # 作者名不在停用词表
        ("Li_2023_Quantum.pdf", "Li", 2023, ["Quantum"],
         "Li 是常见中文姓氏，不应过滤"),
        ("Xu_2023_Graphene.pdf", "Xu", 2023, ["Graphene"],
         "Xu 是常见中文姓氏"),
    ]

    print(f"测试 {len(cases)} 个文件名解析用例...\n")
    for filename, exp_author, exp_year, exp_keywords, desc in cases:
        r = FilenameParser.parse(filename)
        ok = True
        details = []

        if r.author != exp_author:
            ok = False
            details.append(f"author: got={r.author!r}, expected={exp_author!r}")
        if r.year != exp_year:
            ok = False
            details.append(f"year: got={r.year!r}, expected={exp_year!r}")
        # 关键词顺序无关比较
        if set(r.keywords) != set(exp_keywords):
            ok = False
            details.append(f"keywords: got={r.keywords}, expected={exp_keywords}")

        if ok:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {filename}  [{desc}]")
            for d in details:
                print(f"    {d}")

    print()


def test_confidence_scores():
    """测试解析置信度是否合理"""
    global passed, failed
    cases = [
        # (文件名, 期望最低置信度, 说明)
        ("Smith_2023_Quantum.pdf", 0.8, "完整信息: 作者+年份+关键词"),
        ("Smith_2023.pdf", 0.5, "作者+年份"),
        ("2023_Smith_Quantum.pdf", 0.8, "年份前置完整信息"),
        ("Smith_Quantum.pdf", 0.3, "无年份"),
        ("2023_Quantum.pdf", 0.3, "无作者"),
        ("paper.pdf", 0.0, "裸文件名"),
        ("Smith_2023_Deep_Learning.pdf", 0.8, "多词关键词"),
    ]

    print("测试解析置信度...\n")
    for filename, min_conf, desc in cases:
        r = FilenameParser.parse(filename)
        if r.confidence >= min_conf:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {filename}  [{desc}] confidence={r.confidence:.2f} < {min_conf}")

    print()


def test_score_combinations():
    """
    测试 _compute_fuzzy_score 的各种组合。
    """
    global passed, failed
    print("测试评分组合逻辑...\n")
    matcher = DOIMatcher(".")

    # 高置信度: 作者+年份精确+关键词全覆盖 = 100
    parsed = FilenameParseResult(author="Smith", year=2023, keywords=["Quantum"])
    cr = {"author": "John Smith", "title": "Quantum Computing Advances", "year": "2023"}
    score = matcher._compute_fuzzy_score(parsed, cr)
    check("作者+年份+关键词全匹配", score == 100, f"got {score}")
    check("超过自动重命名阈值", score >= FUZZY_AUTO_RENAME_THRESHOLD, f"{score} < {FUZZY_AUTO_RENAME_THRESHOLD}")

    # 作者匹配 + 年份精确 = 80
    parsed2 = FilenameParseResult(author="Smith", year=2023, keywords=["Unrelated"])
    cr2 = {"author": "John Smith", "title": "Something Completely Different", "year": "2023"}
    score2 = matcher._compute_fuzzy_score(parsed2, cr2)
    check("作者+年份精确", score2 == 80, f"got {score2}")
    check("超过自动重命名阈值", score2 >= FUZZY_AUTO_RENAME_THRESHOLD, f"{score2} < {FUZZY_AUTO_RENAME_THRESHOLD}")

    # 仅作者匹配 = 50
    parsed3 = FilenameParseResult(author="Smith", year=None, keywords=[])
    cr3 = {"author": "John Smith", "title": "Anything", "year": "2019"}
    score3 = matcher._compute_fuzzy_score(parsed3, cr3)
    check("仅作者匹配", score3 == 50, f"got {score3}")
    check("低于自动重命名阈值", score3 < FUZZY_AUTO_RENAME_THRESHOLD, f"{score3} >= {FUZZY_AUTO_RENAME_THRESHOLD}")
    check("高于人工确认阈值", score3 >= FUZZY_REVIEW_THRESHOLD, f"{score3} < {FUZZY_REVIEW_THRESHOLD}")

    # 仅年份精确 = 30
    parsed4 = FilenameParseResult(author="", year=2023, keywords=[])
    cr4 = {"author": "Stranger", "title": "Unrelated", "year": "2023"}
    score4 = matcher._compute_fuzzy_score(parsed4, cr4)
    check("仅年份精确", score4 == 30, f"got {score4}")
    check("低于人工确认阈值", score4 < FUZZY_REVIEW_THRESHOLD, f"{score4} >= {FUZZY_REVIEW_THRESHOLD}")

    # 作者匹配 + 年份邻近 = 70
    parsed5 = FilenameParseResult(author="Smith", year=2022, keywords=[])
    cr5 = {"author": "John Smith", "title": "Anything", "year": "2023"}
    score5 = matcher._compute_fuzzy_score(parsed5, cr5)
    check("作者+年份邻近", score5 == 70, f"got {score5}")
    check("达到自动重命名阈值", score5 >= FUZZY_AUTO_RENAME_THRESHOLD, f"{score5} < {FUZZY_AUTO_RENAME_THRESHOLD}")

    # 作者匹配 + 关键词全覆盖 = 70
    parsed6 = FilenameParseResult(author="Smith", year=None, keywords=["Quantum"])
    cr6 = {"author": "John Smith", "title": "Quantum Computing", "year": "2019"}
    score6 = matcher._compute_fuzzy_score(parsed6, cr6)
    check("作者+关键词全覆盖", score6 == 70, f"got {score6}")
    check("达到自动重命名阈值", score6 >= FUZZY_AUTO_RENAME_THRESHOLD, f"{score6} < {FUZZY_AUTO_RENAME_THRESHOLD}")

    # 关键词部分覆盖 = 10
    parsed7 = FilenameParseResult(author="", year=None, keywords=["Quantum", "Computing"])
    cr7 = {"author": "Someone", "title": "Quantum Mechanics Today", "year": "2020"}
    score7 = matcher._compute_fuzzy_score(parsed7, cr7)
    check("关键词部分覆盖(1/2)", score7 == 10, f"got {score7}")

    # 什么都不匹配 = 0
    parsed8 = FilenameParseResult(author="", year=None, keywords=[])
    cr8 = {"author": "Someone", "title": "Anything", "year": "2020"}
    score8 = matcher._compute_fuzzy_score(parsed8, cr8)
    check("无信息", score8 == 0, f"got {score8}")

    # 作者不匹配 + 年份精确 + 关键词全覆盖 = 50
    parsed9 = FilenameParseResult(author="Wrong", year=2023, keywords=["Quantum"])
    cr9 = {"author": "John Smith", "title": "Quantum Computing Advances", "year": "2023"}
    score9 = matcher._compute_fuzzy_score(parsed9, cr9)
    check("作者不匹配但有年份+关键词", score9 == 50, f"got {score9}")

    # 中置信度: 50 在人工确认区间
    check("50 在人工确认区间",
          FUZZY_REVIEW_THRESHOLD <= 50 < FUZZY_AUTO_RENAME_THRESHOLD,
          f"review=[{FUZZY_REVIEW_THRESHOLD}, {FUZZY_AUTO_RENAME_THRESHOLD}) | 50 not in range")

    # 低置信度: 30 低于人工确认阈值
    check("30 低于人工确认阈值",
          30 < FUZZY_REVIEW_THRESHOLD,
          f"30 >= {FUZZY_REVIEW_THRESHOLD}")

    print()


def test_author_dedup():
    """测试作者名在关键词中的去重逻辑"""
    global passed, failed
    cases = [
        ("SMITH_2023_Quantum.pdf", "SMITH", 2023, ["Quantum"],
         "作者大写, 关键词中保留 Quantum"),
        ("smith_2023_quantum.pdf", "smith", 2023, ["quantum"],
         "作者与关键词不同词, quantum 保留"),
        ("Smith_2023_Smith_Smith.pdf", "Smith", 2023, [],
         "作者名重复出现应去重"),
        ("Smith_2023_Research.pdf", "Smith", 2023, [],
         "Research 是停用词, 过滤"),
        ("Wang_2022_Wang_Research.pdf", "Wang", 2022, [],
         "Wang 去重, Research 是停用词"),
    ]

    print("测试作者关键词去重...\n")
    for filename, exp_author, exp_year, exp_keywords, desc in cases:
        r = FilenameParser.parse(filename)
        ok = True
        details = []

        if r.author != exp_author:
            ok = False
            details.append(f"author: got={r.author!r}, expected={exp_author!r}")
        if r.year != exp_year:
            ok = False
            details.append(f"year: got={r.year!r}, expected={exp_year!r}")
        if set(r.keywords) != set(exp_keywords):
            ok = False
            details.append(f"keywords: got={r.keywords}, expected={exp_keywords}")

        if ok:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {filename}  [{desc}]")
            for d in details:
                print(f"    {d}")

    print()


def test_stopwords_filtering():
    """测试停用词过滤是否有效"""
    global passed, failed
    cases = [
        ("the_2023_Quantum.pdf", "", 2023, ["Quantum"],
         "the 是停用词，不应做作者"),
        ("study_2023_Quantum.pdf", "", 2023, ["Quantum"],
         "study 是停用词"),
        ("analysis_2023_Graphene.pdf", "", 2023, ["Graphene"],
         "analysis 是停用词"),
        ("manuscript_2023_Quantum.pdf", "", 2023, ["Quantum"],
         "manuscript 是停用词"),
    ]

    print("测试停用词过滤...\n")
    for filename, exp_author, exp_year, exp_keywords, desc in cases:
        r = FilenameParser.parse(filename)
        ok = True
        details = []

        if r.author != exp_author:
            ok = False
            details.append(f"author: got={r.author!r}, expected={exp_author!r}")
        if r.year != exp_year:
            ok = False
            details.append(f"year: got={r.year!r}, expected={exp_year!r}")
        if set(r.keywords) != set(exp_keywords):
            ok = False
            details.append(f"keywords: got={r.keywords}, expected={exp_keywords}")

        if ok:
            passed += 1
        else:
            failed += 1
            print(f"  FAIL: {filename}  [{desc}]")
            for d in details:
                print(f"    {d}")

    print()


def test_xmol_checklist_integration():
    """测试模糊匹配结果能正确生成 x-mol 人工确认清单"""
    global passed, failed

    review_items = [
        {
            "pdf_name": "Smith_2023_Quantum.pdf",
            "final_title": "Quantum Computing",
            "cr_doi": "10.1000/quantum.2023.001",
            "fuzzy_score": 55,
        },
        {
            "pdf_name": "Wang_2022.pdf",
            "final_title": "Graphene Synthesis",
            "cr_doi": "10.1000/graphene.2022.042",
            "fuzzy_score": 60,
        },
    ]

    checklist = XMolFallback.generate_checklist(review_items)
    assert "Smith_2023_Quantum.pdf" in checklist
    assert "Wang_2022.pdf" in checklist
    assert "10.1000/quantum.2023.001" in checklist
    assert "x-mol.net" in checklist
    passed += 1
    print("  ✅ x-mol 确认清单生成正常")


if __name__ == "__main__":
    print("=" * 60)
    print("模糊文件名匹配 — 大规模验证")
    print("=" * 60)
    print()

    test_parser_variants()
    test_confidence_scores()
    test_score_combinations()
    test_author_dedup()
    test_stopwords_filtering()
    test_xmol_checklist_integration()

    print("=" * 60)
    total = passed + failed
    print(f"结果: {passed}/{total} 通过", end="")
    if failed > 0:
        print(f", {failed} 失败")
    else:
        print(" -- 全部通过")
    print("=" * 60)

    sys.exit(1 if failed > 0 else 0)