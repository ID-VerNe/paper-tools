import os
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup
import html2text
import re

class EPUBConverter:
    def __init__(self):
        self.h = html2text.HTML2Text()
        self.h.ignore_links = False
        self.h.ignore_images = False
        self.h.body_width = 0  # 不自动换行
        self.h.unicode_snob = True
        self.h.skip_internal_links = True

    def extract_images(self, book, images_dir):
        """提取 EPUB 中的图片到指定目录"""
        os.makedirs(images_dir, exist_ok=True)
        image_map = {}  # 原始路径 -> 新路径的映射

        img_idx = 0
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_IMAGE:
                # 获取原始文件名和扩展名
                original_name = item.get_name()
                ext = os.path.splitext(original_name)[1] or '.jpg'

                # 保存图片
                new_filename = f"img_{img_idx}{ext}"
                img_path = os.path.join(images_dir, new_filename)

                with open(img_path, 'wb') as f:
                    f.write(item.get_content())

                # 记录映射关系
                image_map[original_name] = f"images/{new_filename}"
                img_idx += 1

        return image_map

    def clean_html(self, html_content):
        """清理 HTML，移除样式等无关内容"""
        soup = BeautifulSoup(html_content, 'html.parser')

        # 移除 style 和 script 标签
        for tag in soup(['style', 'script']):
            tag.decompose()

        return str(soup)

    def fix_image_paths(self, markdown_content, image_map):
        """修复 Markdown 中的图片路径"""
        for original_path, new_path in image_map.items():
            # 处理各种可能的图片路径格式
            patterns = [
                rf'!\[([^\]]*)\]\(([^)]*{re.escape(original_path)}[^)]*)\)',
                rf'!\[([^\]]*)\]\({re.escape(original_path)}\)',
            ]

            for pattern in patterns:
                markdown_content = re.sub(
                    pattern,
                    rf'![\1]({new_path})',
                    markdown_content
                )

        return markdown_content

    def convert_epub_to_markdown(self, epub_path, output_dir, progress_callback=None):
        """
        转换 EPUB 文件为 Markdown

        Args:
            epub_path: EPUB 文件路径
            output_dir: 输出目录
            progress_callback: 进度回调函数 (current, total, message)

        Returns:
            生成的 Markdown 文件路径
        """
        try:
            # 读取 EPUB
            book = epub.read_epub(epub_path)
            name = os.path.splitext(os.path.basename(epub_path))[0].strip()

            # 创建输出目录
            out_dir = os.path.join(output_dir, name)
            os.makedirs(out_dir, exist_ok=True)
            images_dir = os.path.join(out_dir, "images")

            if progress_callback:
                progress_callback(0, 100, f"Processing: {name}")

            # 提取图片
            image_map = self.extract_images(book, images_dir)

            if progress_callback:
                progress_callback(20, 100, f"Extracted {len(image_map)} images")

            # 提取所有文档内容
            markdown_parts = []
            doc_items = [item for item in book.get_items() if item.get_type() == ebooklib.ITEM_DOCUMENT]
            total_items = len(doc_items)

            for idx, item in enumerate(doc_items):
                try:
                    # 解码 HTML 内容
                    html_content = item.get_content().decode('utf-8', errors='ignore')

                    # 清理 HTML
                    html_content = self.clean_html(html_content)

                    # 转换为 Markdown
                    md = self.h.handle(html_content)

                    # 清理空白行
                    md = re.sub(r'\n{3,}', '\n\n', md)

                    if md.strip():
                        markdown_parts.append(md)

                    if progress_callback:
                        progress = 20 + int((idx + 1) / total_items * 70)
                        progress_callback(progress, 100, f"Converting: {idx + 1}/{total_items}")

                except Exception as e:
                    # 单个章节失败不影响整体
                    markdown_parts.append(f"\n\n> [Error processing chapter: {str(e)}]\n\n")

            # 合并所有内容
            final_markdown = '\n\n---\n\n'.join(markdown_parts)

            # 修复图片路径
            final_markdown = self.fix_image_paths(final_markdown, image_map)

            # 保存 Markdown 文件
            output_file = os.path.join(out_dir, f"{name}.md")
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(final_markdown)

            if progress_callback:
                progress_callback(100, 100, f"Completed: {name}")

            return output_file

        except Exception as e:
            error_msg = f"Failed to convert {epub_path}: {str(e)}"
            if progress_callback:
                progress_callback(0, 100, error_msg)
            raise Exception(error_msg)
