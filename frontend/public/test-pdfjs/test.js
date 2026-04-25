// PDF.js 测试工具
import * as pdfjsLib from 'https://unpkg.com/pdfjs-dist@4.4.168/build/pdf.min.mjs';

// 配置 worker
pdfjsLib.GlobalWorkerOptions.workerSrc =
    'https://unpkg.com/pdfjs-dist@4.4.168/build/pdf.worker.min.mjs';

let pdfDoc = null;
let currentPageNum = 1;
let currentSearchText = '';
let currentScale = 1.5;

// DOM 元素
const pdfUrlInput = document.getElementById('pdf-url');
const pageNumInput = document.getElementById('page-num');
const loadBtn = document.getElementById('load-btn');
const searchTextInput = document.getElementById('search-text');
const searchBtn = document.getElementById('search-btn');
const clearBtn = document.getElementById('clear-btn');
const pdfContainer = document.getElementById('pdf-container');
const textOutput = document.getElementById('text-output');
const totalPagesEl = document.getElementById('total-pages');
const currentPageEl = document.getElementById('current-page');
const textLengthEl = document.getElementById('text-length');
const matchCountEl = document.getElementById('match-count');

// 加载 PDF
loadBtn.addEventListener('click', async () => {
    const url = pdfUrlInput.value.trim();
    const pageNum = parseInt(pageNumInput.value) || 1;

    if (!url) {
        alert('请输入 PDF URL');
        return;
    }

    loadBtn.disabled = true;
    loadBtn.textContent = '加载中...';
    pdfContainer.innerHTML = '<div style="padding: 50px; text-align: center; color: #888;">⏳ 正在加载 PDF...</div>';
    textOutput.textContent = '';

    try {
        console.log('📄 加载 PDF:', url);

        // 加载 PDF 文档
        pdfDoc = await pdfjsLib.getDocument({
            url: url,
            cMapUrl: 'https://unpkg.com/pdfjs-dist@4.4.168/cmaps/',
            cMapPacked: true,
        }).promise;

        console.log('✅ PDF 加载成功, 总页数:', pdfDoc.numPages);
        totalPagesEl.textContent = pdfDoc.numPages;
        currentPageNum = Math.min(pageNum, pdfDoc.numPages);
        currentPageEl.textContent = currentPageNum;

        // 渲染指定页面
        await renderPage(currentPageNum);

    } catch (error) {
        console.error('❌ 加载失败:', error);
        pdfContainer.innerHTML = `<div class="error">加载失败: ${error.message}</div>`;
    } finally {
        loadBtn.disabled = false;
        loadBtn.textContent = '加载 PDF';
    }
});

// 渲染页面
async function renderPage(pageNum) {
    if (!pdfDoc) return;

    pdfContainer.innerHTML = '<div style="padding: 50px; text-align: center; color: #888;">⏳ 渲染页面...</div>';

    try {
        const page = await pdfDoc.getPage(pageNum);
        const viewport = page.getViewport({ scale: currentScale });

        // 创建 canvas
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        canvas.height = viewport.height;
        canvas.width = viewport.width;

        // 创建页面容器
        const pageContainer = document.createElement('div');
        pageContainer.className = 'page-container';
        pageContainer.style.position = 'relative';
        pageContainer.style.width = viewport.width + 'px';
        pageContainer.style.height = viewport.height + 'px';

        // 先渲染到 canvas
        await page.render({
            canvasContext: context,
            viewport: viewport,
        }).promise;

        pageContainer.appendChild(canvas);

        // 提取文本内容
        const textContent = await page.getTextContent();
        console.log('📝 文本项数量:', textContent.items.length);

        // 构建纯文本
        let fullText = '';
        const textItems = textContent.items;

        // 按 Y 坐标分组（同一行）
        const lines = {};
        textItems.forEach(item => {
            const y = Math.round(item.transform[5]);
            if (!lines[y]) lines[y] = [];
            lines[y].push(item);
        });

        // 按 Y 坐标排序
        const sortedY = Object.keys(lines).sort((a, b) => b - a);

        sortedY.forEach(y => {
            // 按 X 坐标排序同一行内的文本
            const line = lines[y].sort((a, b) => a.transform[4] - b.transform[4]);
            const lineText = line.map(item => item.str).join('');
            fullText += lineText + '\n';
        });

        console.log('📄 提取的文本:');
        console.log(fullText);

        // 显示文本
        textOutput.textContent = fullText;
        textLengthEl.textContent = fullText.length;

        // 创建文本层（只在 canvas 上面覆盖透明的文本层）
        const textLayerDiv = document.createElement('div');
        textLayerDiv.className = 'text-layer';
        textLayerDiv.style.position = 'absolute';
        textLayerDiv.style.left = '0';
        textLayerDiv.style.top = '0';
        textLayerDiv.style.width = viewport.width + 'px';
        textLayerDiv.style.height = viewport.height + 'px';

        // 为每个文本项创建 span
        textContent.items.forEach((item, index) => {
            const tx = pdfjsLib.Util.transform(
                viewport.transform,
                item.transform
            );

            const angle = Math.atan2(tx[1], tx[0]);
            const style = textContent.styles[item.fontName] || {};

            const span = document.createElement('span');
            span.textContent = item.str;
            span.style.position = 'absolute';
            span.style.left = tx[4] + 'px';
            span.style.top = (tx[5] - item.height * 0.8) + 'px';
            span.style.fontSize = Math.sqrt(tx[2] * tx[2] + tx[3] * tx[3]) + 'px';
            span.style.fontFamily = style.fontFamily || 'sans-serif';
            span.style.color = 'transparent';

            if (item.width > 0) {
                span.style.minWidth = item.width * Math.sqrt(tx[0] * tx[0] + tx[1] * tx[1]) + 'px';
            }

            textLayerDiv.appendChild(span);
        });

        pageContainer.appendChild(textLayerDiv);

        // 更新容器
        pdfContainer.innerHTML = '';
        pdfContainer.appendChild(pageContainer);

        console.log('✅ 页面渲染完成');

        // 输出带位置的文本详情
        console.log('📋 文本详情（前20项）:');
        textContent.items.slice(0, 20).forEach((item, i) => {
            console.log(`  [${i}] "${item.str}"`, {
                height: item.height,
                width: item.width,
                x: item.transform[4],
                y: item.transform[5],
                fontName: item.fontName
            });
        });

        // 自动搜索
        if (currentSearchText) {
            searchInPage(fullText, currentSearchText);
        }

    } catch (error) {
        console.error('❌ 渲染失败:', error);
        pdfContainer.innerHTML = `<div class="error">
            <p>渲染失败: ${error.message}</p>
            <p style="font-size: 12px; color: #999;">堆栈: ${error.stack}</p>
        </div>`;
    }
}

// 搜索文本
searchBtn.addEventListener('click', () => {
    const searchText = searchTextInput.value.trim();
    if (!searchText) {
        alert('请输入搜索文本');
        return;
    }

    currentSearchText = searchText;

    if (!textOutput.textContent) {
        alert('请先加载 PDF');
        return;
    }

    searchInPage(textOutput.textContent, searchText);
});

// 清除高亮
clearBtn.addEventListener('click', () => {
    currentSearchText = '';
    searchTextInput.value = '';
    textOutput.innerHTML = textOutput.textContent;
    matchCountEl.textContent = '0';
    console.log('🔄 已清除高亮');
});
// 在搜索函数中添加 PDF 层高亮
function searchInPage(text, searchText) {
    const escapedSearch = searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(escapedSearch, 'gi');

    const matches = text.match(regex) || [];
    matchCountEl.textContent = matches.length;

    console.log(`🔍 搜索 "${searchText}"`);
    console.log(`  - 匹配数: ${matches.length}`);
    console.log(`  - 匹配内容:`, matches);

    // 高亮右侧文本面板
    if (matches.length > 0) {
        const highlightedText = text.replace(regex, match =>
            `<span style="background:#0f0;color:#000;font-weight:bold;padding:2px 6px;border-radius:3px;box-shadow:0 0 10px rgba(0,255,0,0.9);outline:2px solid #0a0;outline-offset:1px;">${match}</span>`
        );
        textOutput.innerHTML = highlightedText;
    } else {
        textOutput.innerHTML = text;
        console.log('  ⚠️ 未找到匹配');
    }

    // ✅ 在 PDF 渲染层上高亮
    highlightInPdfLayer(searchText);
}

// 新增：在 PDF 文本层上高亮
function highlightInPdfLayer(searchText) {
    // 清除之前的高亮
    clearPdfHighlights();

    const textLayer = document.querySelector('.text-layer');
    if (!textLayer) {
        console.log('⚠️ 未找到文本层');
        return;
    }

    const spans = textLayer.querySelectorAll('span');
    let foundCount = 0;

    spans.forEach(span => {
        const text = span.textContent;
        if (text.includes(searchText)) {
            // 高亮匹配的 span
            span.style.color = 'transparent';
            span.style.backgroundColor = 'rgba(7, 255, 48, 1)'; // 黄色半透明
            span.style.borderRadius = '2px';
            span.style.padding = '2px';
            foundCount++;

            console.log(`✅ 在 PDF 层找到匹配: "${text}"`);
        }
    });

    if (foundCount > 0) {
        console.log(`✅ PDF 层高亮了 ${foundCount} 个文本块`);
    } else {
        console.log('⚠️ PDF 层未找到匹配文本');

        // 输出前10个文本块内容，帮助诊断
        console.log('📋 PDF 层前10个文本块:');
        spans.slice(0, 10).forEach((span, i) => {
            console.log(`  [${i}] "${span.textContent}"`);
        });
    }
}

// 清除 PDF 高亮
function clearPdfHighlights() {
    const textLayer = document.querySelector('.text-layer');
    if (!textLayer) return;

    const spans = textLayer.querySelectorAll('span');
    spans.forEach(span => {
        span.style.backgroundColor = 'transparent';
        span.style.padding = '0';
    });
}

// 修改清除按钮
clearBtn.addEventListener('click', () => {
    currentSearchText = '';
    searchTextInput.value = '';
    textOutput.innerHTML = textOutput.textContent;
    matchCountEl.textContent = '0';
    clearPdfHighlights();
    console.log('🔄 已清除高亮');
});

// // 在页面中搜索并高亮
// function searchInPage(text, searchText) {
//     const escapedSearch = searchText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
//     const regex = new RegExp(escapedSearch, 'gi');

//     const matches = text.match(regex) || [];
//     matchCountEl.textContent = matches.length;

//     console.log(`🔍 搜索 "${searchText}"`);
//     console.log(`  - 匹配数: ${matches.length}`);
//     console.log(`  - 匹配内容:`, matches);

//     if (matches.length > 0) {
//         // 高亮显示
//         const highlightedText = text.replace(regex, match =>
//             `<span class="highlight">${match}</span>`
//         );
//         textOutput.innerHTML = highlightedText;

//         // 显示每个匹配的上下文
//         matches.forEach((match, i) => {
//             const index = text.indexOf(match);
//             const start = Math.max(0, index - 30);
//             const end = Math.min(text.length, index + match.length + 30);
//             console.log(`  匹配 ${i + 1}: "...${text.substring(start, end)}..."`);
//         });
//     } else {
//         textOutput.innerHTML = text;
//         console.log('  ⚠️ 未找到匹配');

//         // 尝试部分匹配（数字）
//         if (/^\d+$/.test(searchText)) {
//             console.log('  💡 尝试查找所有数字:');
//             const allNumbers = text.match(/\d+/g) || [];
//             console.log('  ', allNumbers);
//         }
//     }
// }

// 页面切换
pageNumInput.addEventListener('change', async () => {
    const newPage = parseInt(pageNumInput.value) || 1;
    if (pdfDoc && newPage >= 1 && newPage <= pdfDoc.numPages) {
        currentPageNum = newPage;
        currentPageEl.textContent = currentPageNum;
        await renderPage(currentPageNum);
    }
});

// 键盘导航
document.addEventListener('keydown', async (e) => {
    if (!pdfDoc) return;

    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        if (currentPageNum > 1) {
            currentPageNum--;
            pageNumInput.value = currentPageNum;
            currentPageEl.textContent = currentPageNum;
            await renderPage(currentPageNum);
        }
    } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        if (currentPageNum < pdfDoc.numPages) {
            currentPageNum++;
            pageNumInput.value = currentPageNum;
            currentPageEl.textContent = currentPageNum;
            await renderPage(currentPageNum);
        }
    }
});

console.log('🚀 PDF.js 测试工具已就绪');
console.log('💡 使用方法:');
console.log('  1. 输入 PDF URL');
console.log('  2. 输入页码（默认第6页）');
console.log('  3. 点击"加载 PDF"');
console.log('  4. 查看右侧提取的文本');
console.log('  5. 搜索 "7名" 或 "7" 查看 PDF.js 实际识别的文本');
console.log('  6. 使用 ← → 键翻页');
console.log('  7. 打开浏览器控制台查看详细信息');