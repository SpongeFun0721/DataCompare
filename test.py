"""
PDF 图片格式检测脚本
用于检测 PDF 中的图片是 JPEG、JPEG 2000 还是其他格式
"""

import sys
import os

try:
    import fitz  # PyMuPDF
except ImportError:
    print("正在安装 PyMuPDF...")
    os.system(f"{sys.executable} -m pip install PyMuPDF")
    import fitz

def detect_pdf_image_formats(pdf_path, show_details=False):
    """
    检测 PDF 中所有图片的格式
    
    参数:
        pdf_path: PDF 文件路径
        show_details: 是否显示详细信息（图片尺寸、颜色空间等）
    """
    
    if not os.path.exists(pdf_path):
        print(f"❌ 文件不存在: {pdf_path}")
        return
    
    print(f"\n{'='*60}")
    print(f"📄 正在分析: {os.path.basename(pdf_path)}")
    print(f"{'='*60}\n")
    
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"❌ 无法打开 PDF: {e}")
        return
    
    # 格式映射
    FORMAT_MAP = {
        'jpeg': ('JPEG', '✅ 正常', 'jpg'),
        'jpx': ('JPEG 2000', '⚠️ 可能需要额外解码器', 'jp2'),
        'png': ('PNG', '✅ 正常', 'png'),
        'tiff': ('TIFF', '✅ 通常正常', 'tiff'),
        'jb2': ('JBIG2', '⚠️ 可能需要额外解码器', 'jb2'),
        'ccitt': ('CCITT Fax', '✅ 正常（黑白图）', 'fax'),
    }
    
    total_images = 0
    jpeg_count = 0
    jpx_count = 0
    other_count = 0
    image_list = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        images = page.get_images(full=True)
        
        if images:
            for img_idx, img in enumerate(images):
                total_images += 1
                xref = img[0]
                
                try:
                    base_image = doc.extract_image(xref)
                    ext = base_image["ext"]
                    width = base_image["width"]
                    height = base_image["height"]
                    colorspace = base_image.get("colorspace", "未知")
                    image_size = len(base_image["image"])  # 字节大小
                    
                    # 判断格式
                    if ext == 'jpeg':
                        format_name, status, _ = FORMAT_MAP['jpeg']
                        jpeg_count += 1
                    elif ext == 'jpx':
                        format_name, status, _ = FORMAT_MAP['jpx']
                        jpx_count += 1
                    else:
                        format_name, status, _ = FORMAT_MAP.get(
                            ext, (ext.upper(), '❓ 未知', ext)
                        )
                        other_count += 1
                    
                    image_info = {
                        'page': page_num + 1,
                        'index': img_idx + 1,
                        'format': ext,
                        'format_name': format_name,
                        'status': status,
                        'width': width,
                        'height': height,
                        'colorspace': colorspace,
                        'size_kb': round(image_size / 1024, 2)
                    }
                    image_list.append(image_info)
                    
                    if show_details:
                        print(f"📑 第 {page_num+1} 页 - 图片 {img_idx+1}:")
                        print(f"   格式: {format_name} ({ext}) {status}")
                        print(f"   尺寸: {width}x{height}")
                        print(f"   颜色空间: {colorspace}")
                        print(f"   文件大小: {image_info['size_kb']} KB")
                        print()
                    
                except Exception as e:
                    print(f"❌ 第 {page_num+1} 页 图片 {img_idx+1} 提取失败: {e}")
    
    doc.close()
    
    # 汇总报告
    print(f"{'='*60}")
    print(f"📊 检测汇总")
    print(f"{'='*60}")
    print(f"总图片数: {total_images}")
    print(f"  ✅ 普通 JPEG: {jpeg_count} 张")
    print(f"  ⚠️  JPEG 2000: {jpx_count} 张")
    print(f"  •  其他格式: {other_count} 张")
    print()
    
    if jpx_count > 0:
        print("⚠️  警告: 该 PDF 包含 JPEG 2000 格式图片！")
        print("    JPEG 2000 在网页/pdf.js 中可能无法正常显示。")
        print("    建议使用 Ghostscript 或 Acrobat 转换为标准 JPEG 格式。")
        print(f"    受影响的页面: {set([img['page'] for img in image_list if img['format'] == 'jpx'])}")
    else:
        print("✅ 未检测到 JPEG 2000 格式，兼容性良好。")
    
    print(f"\n{'='*60}")
    
    return image_list


def batch_detect(folder_path):
    """批量检测文件夹中所有 PDF"""
    pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print(f"❌ 在 {folder_path} 中未找到 PDF 文件")
        return
    
    print(f"\n找到 {len(pdf_files)} 个 PDF 文件\n")
    
    for pdf_file in pdf_files:
        pdf_path = os.path.join(folder_path, pdf_file)
        detect_pdf_image_formats(pdf_path, show_details=False)
        print()  # 空行分隔


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='检测 PDF 中的图片格式')
    parser.add_argument('path', help='PDF 文件路径或包含 PDF 的文件夹路径')
    parser.add_argument('-d', '--details', action='store_true', 
                       help='显示每张图片的详细信息')
    parser.add_argument('-b', '--batch', action='store_true',
                       help='批量检测文件夹中的所有 PDF')
    
    args = parser.parse_args()
    
    if args.batch and os.path.isdir(args.path):
        batch_detect(args.path)
    else:
        detect_pdf_image_formats(args.path, show_details=args.details)