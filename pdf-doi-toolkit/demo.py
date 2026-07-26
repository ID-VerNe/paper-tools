"""
pdf-doi-toolkit — 使用示例
"""

# =============================================================================
# 示例 1: 一键全自动
# =============================================================================
from pdf_doi_toolkit import simple_rename_pipeline

matcher = simple_rename_pipeline("path/to/pdf/directory")
print(matcher.summary())

# =============================================================================
# 示例 2: 分步控制（推荐——更灵活）
# =============================================================================
from pdf_doi_toolkit import DOIMatcher, CrossRefClient, XMolFallback

# 创建匹配器
matcher = DOIMatcher(
    summary_dir="path/to/pdf/directory",
    output_dir="path/to/reports",  # 可选
)

# 第 1 步：扫描
matcher.scan_pdfs()
print(f"A 组（有 title+author）: {len(matcher.cat_a)}")
print(f"B 组（有 title 无 author）: {len(matcher.cat_b)}")
print(f"C 组（无 title）: {len(matcher.cat_c)}")

# 第 2 步：CrossRef 查询
matcher.run_crossref_check()

# 第 3 步：重命名匹配的
renamed = matcher.rename_matched()
print(f"已重命名: {renamed} 篇")

# 第 4 步：保存报告
matcher.save_report()

# =============================================================================
# 示例 3: 处理 ScienceDirect 格式
# =============================================================================
matcher = DOIMatcher("path/to/pdf/directory")
matcher.scan_sciencedirect()
matcher.run_crossref_check()
matcher.rename_matched()

# =============================================================================
# 示例 4: 用户从 x-mol 确认后执行
# =============================================================================
matcher = DOIMatcher("path/to/pdf/directory")

# 生成 x-mol 确认清单
checklist = XMolFallback.generate_checklist(
    matcher.entries,
    output_path="checklist.md",
)

# 用户告诉你后，执行
matcher.verify_manual_dois({
    "old_name.pdf": "10.xxxx/xxxxx",
    "another.pdf": "10.xxxx/yyyyy",
})

# =============================================================================
# 示例 5: 单独使用 CrossRef 查询
# =============================================================================
from pdf_doi_toolkit import CrossRefClient

cr = CrossRefClient()
res = cr.search_by_title("论文标题", "期望作者")
print(f"DOI: {res['doi']}")
print(f"作者: {res['author']}")

# 按 DOI 查
res2 = cr.search_by_doi("10.xxxx/xxxxx")
print(f"标题: {res2['title']}")
print(f"作者: {res2['author']}")