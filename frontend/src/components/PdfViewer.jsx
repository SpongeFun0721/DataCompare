import { useState, useRef, useEffect, useCallback } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';


pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;


console.log('📦 PDF.js 配置:');
console.log('   版本:', `${pdfjs.version}`);
console.log('   Worker:', pdfjs.GlobalWorkerOptions.workerSrc);
console.log('   WASM 路径:', pdfjs.GlobalWorkerOptions.wasmUrl);
const style = document.createElement('style');
style.textContent = `
  .pdf-highlight-mark {
    background-color: rgba(255, 255, 0, 0.9) !important;
    color: #000 !important;
    font-weight: bold !important;
    padding: 2px 4px !important;
    border-radius: 3px !important;
    box-shadow: 0 0 10px rgba(255, 200, 0, 0.8) !important;
    outline: 2px solid rgba(255, 100, 0, 0.9) !important;
    outline-offset: 1px !important;
  }
  .text-panel-highlight {
    background-color: rgba(255, 255, 0, 0.7);
    color: #000;
    font-weight: bold;
    padding: 1px 3px;
    border-radius: 2px;
  }

`;
document.head.appendChild(style);


export default function PdfViewer({ pdfUrl, targetPage, highlightText, triggerKey, onTextSelect, onPageChange, onNumPagesChange }) {
  const [numPages, setNumPages] = useState(null);
  const [currentPage, setCurrentPage] = useState(targetPage || 1);
  const [scale, setScale] = useState(1.0);
  const containerRef = useRef(null);
  const pdfDocRef = useRef(null);
  const [selection, setSelection] = useState(null);
  const [extractedText, setExtractedText] = useState('');
  const [showTextPanel, setShowTextPanel] = useState(true);
  const [loadError, setLoadError] = useState(null);


  const extractPageText = useCallback(async (pageNum) => {
    if (!pdfDocRef.current) return;
    try {
        const page = await pdfDocRef.current.getPage(pageNum);
        const textContent = await page.getTextContent();
        
        let fullText = '';
        const lines = {};
        textContent.items.forEach(item => {
            const y = Math.round(item.transform[5]);
            if (!lines[y]) lines[y] = [];
            lines[y].push(item);
        });
        
        Object.keys(lines)
            .sort((a, b) => b - a)
            .forEach(y => {
                const line = lines[y].sort((a, b) => a.transform[4] - b.transform[4]);
                fullText += line.map(item => item.str.replace(/[\s\u3000]/g, '')).join('') + '\n';
            });
        
        setExtractedText(fullText);
    } catch (error) {
        console.error('文本提取失败:', error);
    }
  }, []);

    // triggerKey 变化时同步（响应代码跳转）
  useEffect(() => {
    if (targetPage && targetPage !== currentPage) {
      setCurrentPage(targetPage);
    }
  }, [triggerKey]);

  // highlightText 变化时重新提取文本（更新右侧文本面板）
  useEffect(() => {
    if (currentPage) {
        extractPageText(currentPage);
    }
  }, [highlightText, currentPage, extractPageText]);

  // 当外部 targetPage 变化时同步（快捷键 PgUp/PgDn）
  useEffect(() => {
    if (targetPage && targetPage !== currentPage) {
      setCurrentPage(targetPage);
    }
  }, [targetPage]);

  // 通知父组件 numPages
  useEffect(() => {
    if (numPages && onNumPagesChange) {
      onNumPagesChange(numPages);
    }
  }, [numPages, onNumPagesChange]);

  // 通知父组件页面变化
  useEffect(() => {
    if (onPageChange) {
      onPageChange(currentPage);
    }
  }, [currentPage, onPageChange]);

function onDocumentLoadSuccess(pdf) {
    pdfDocRef.current = pdf;
    setNumPages(pdf.numPages);
    console.log('✅ PDF 加载成功');
    console.log('   - 页数:', pdf.numPages);
    console.log('   - pdfjs 版本:', pdfjs.version);
    console.log('   - worker 配置:', pdfjs.GlobalWorkerOptions.workerSrc);
    extractPageText(currentPage);
}

  // 渲染文本层后的自定义高亮逻辑（单 span 匹配）
  // 使用正则匹配，支持单位后缀（如 highlightText="17人" 时匹配 "17"、"17人"、"17例" 等）
  const customTextRenderer = useCallback(({ str, itemIndex }) => {
    const spacelessStr = str.replace(/[\s\u3000]/g, '');
    if (!highlightText) return spacelessStr;

    const highlightStr = String(highlightText).replace(/[\s\u3000]/g, '');
    if (!highlightStr) return spacelessStr;

    // 提取高亮文本中的数值部分（如 "17人" → "17"）
    const numMatch = highlightStr.match(/[\d,]+\.?\d*/);
    if (!numMatch) {
      // 没有数值，退回到精确包含匹配
      if (spacelessStr.includes(highlightStr)) {
        return spacelessStr.replace(
          new RegExp(highlightStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'),
          match => `<mark class="pdf-highlight-mark">${match}</mark>`
        );
      }
      return spacelessStr;
    }

    const num = numMatch[0].replace(/,/g, '');
    // 正则：匹配该数值，允许后面跟任意非数字字符（单位后缀）
    // 使用负向前瞻避免匹配到更大数值的一部分（如 "17" 不匹配 "170"）
    const regex = new RegExp(
      `(?<![0-9.,])${num.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[^0-9.,]*`,
      'g'
    );

    return spacelessStr.replace(regex, match => {
      // 只高亮数值部分，单位部分不高亮
      const valMatch = match.match(/[\d,]+\.?\d*/);
      if (valMatch && valMatch[0] === num) {
        return `<mark class="pdf-highlight-mark">${match}</mark>`;
      }
      return match;
    });
  }, [highlightText]);


  // ============================================================
  // 跨 span 文本高亮：通过 DOM 操作实现
  // 解决 PDF 文本层将关键词拆分到多个相邻 span 中无法匹配的问题
  // 使用 MutationObserver 监听文本层 DOM 变化，确保渲染完成后执行
  // 匹配成功后，分别高亮每个 span（仅背景色，不加边框避免视觉断裂）
  // ============================================================
  useEffect(() => {
    // 1. 先清除之前的高亮
    document.querySelectorAll('.react-pdf__Page__textContent span[role="presentation"]').forEach(el => {
      el.style.backgroundColor = '';
      el.style.fontWeight = '';
    });

    if (!highlightText) return;

    const target = String(highlightText).replace(/[\s\u3000]/g, '');
    if (!target) return;

    const HIGHLIGHT_STYLE = {
      backgroundColor: 'rgba(255, 255, 0, 0.4)',
      fontWeight: 'bold',
    };

    // 提取高亮文本中的数值部分（如 "17人" → "17"），用于正则匹配
    const numMatch = target.match(/[\d,]+\.?\d*/);
    const matchNum = numMatch ? numMatch[0].replace(/,/g, '') : null;

    const doHighlight = () => {
      const textContent = document.querySelector('.react-pdf__Page__textContent');
      if (!textContent) return false;

      const spans = Array.from(textContent.querySelectorAll('span[role="presentation"]'));
      if (spans.length === 0) return false;

      const getSpanText = (span) => span.textContent.replace(/[\s\u3000]/g, '');

      // 辅助函数：检查文本是否匹配高亮目标（支持单位后缀）
      const isMatch = (text) => {
        if (text.includes(target)) return true;
        // 如果高亮文本包含数值，尝试只匹配数值部分（允许单位后缀）
        if (matchNum) {
          const regex = new RegExp(
            `(?<![0-9.,])${matchNum.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[^0-9.,]*`
          );
          return regex.test(text);
        }
        return false;
      };

      // 2. 先尝试在单个 span 中匹配
      for (const span of spans) {
        if (isMatch(getSpanText(span))) {
          Object.assign(span.style, HIGHLIGHT_STYLE);
          console.log(`✅ 单 span 高亮: "${target}"`);
          return true;
        }
      }

      // 3. 单个 span 匹配失败，尝试合并相邻 span
      for (let i = 0; i < spans.length; i++) {
        let mergedText = getSpanText(spans[i]);
        if (!mergedText) continue;

        for (let j = i + 1; j < spans.length; j++) {
          mergedText += getSpanText(spans[j]);

          if (isMatch(mergedText)) {
            // 匹配成功，分别高亮每个 span（仅背景色，无边框）
            for (let k = i; k <= j; k++) {
              Object.assign(spans[k].style, HIGHLIGHT_STYLE);
            }
            console.log(`✅ 跨 ${j - i + 1} 个 span 高亮: "${target}" (span[${i}]~span[${j}])`);
            return true;
          }

          if (mergedText.length > target.length + 30) {
            break;
          }
        }
      }

      return false;
    };

    // 立即尝试
    if (doHighlight()) return;

    // 使用 MutationObserver 监听文本层 DOM 变化
    const textContent = document.querySelector('.react-pdf__Page__textContent');
    if (textContent) {
      const observer = new MutationObserver(() => {
        if (doHighlight()) {
          observer.disconnect();
        }
      });
      observer.observe(textContent, { childList: true, subtree: true, characterData: true });
      // 5 秒超时断开
      const timeout = setTimeout(() => observer.disconnect(), 5000);
      return () => {
        observer.disconnect();
        clearTimeout(timeout);
      };
    }

    // 文本层还不存在，监听 body 变化
    const bodyObserver = new MutationObserver(() => {
      const tc = document.querySelector('.react-pdf__Page__textContent');
      if (tc) {
        if (doHighlight()) {
          bodyObserver.disconnect();
          return;
        }
        // 文本层存在但 span 为空，监听文本层
        const observer = new MutationObserver(() => {
          if (doHighlight()) {
            observer.disconnect();
          }
        });
        observer.observe(tc, { childList: true, subtree: true, characterData: true });
        setTimeout(() => observer.disconnect(), 5000);
        bodyObserver.disconnect();
      }
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true });
    const timeout = setTimeout(() => bodyObserver.disconnect(), 5000);
    return () => {
      bodyObserver.disconnect();
      clearTimeout(timeout);
    };
  }, [highlightText, currentPage]);








  // 监听鼠标抬起，获取选中的文本
  const handleMouseUp = () => {
    const activeSelection = window.getSelection();
    if (!activeSelection || activeSelection.isCollapsed) {
      setSelection(null);
      return;
    }

    // 确保选区在 PDF 页面内，而不是选中了报错文本或工具栏
    const anchorNode = activeSelection.anchorNode;
    if (!anchorNode || !anchorNode.parentElement?.closest('.react-pdf__Page')) {
      setSelection(null);
      return;
    }

    const text = activeSelection.toString().trim();
    if (text) {
      const range = activeSelection.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      const containerRect = containerRef.current.getBoundingClientRect();

      setSelection({
        text,
        x: rect.left - containerRect.left + (rect.width / 2),
        y: rect.top - containerRect.top - 40, // 弹窗在选区上方
      });
    }
  };

  const handleBindClick = () => {
    if (selection && onTextSelect) {
      onTextSelect(selection.text, currentPage);
      setSelection(null);
      window.getSelection().removeAllRanges();
    }
  };

  function onDocumentLoadError(error) {
    console.error('❌ PDF 加载失败:', {
      message: error.message,
      name: error.name,
      stack: error.stack,
      pdfUrl: pdfUrl
    });
    setLoadError(error.message || '未知错误');
  }

  useEffect(() => {
    console.log('📊 PdfViewer Props 更新:');
    console.log('  - pdfUrl:', pdfUrl);
    console.log('  - targetPage:', targetPage);
    console.log('  - highlightText:', highlightText);
    console.log('  - onTextSelect:', typeof onTextSelect);
  }, [pdfUrl, targetPage, highlightText, onTextSelect]);


  // 点击空白处取消选择
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (selection && !e.target.closest('.bind-popup')) {
        setSelection(null);
        window.getSelection().removeAllRanges();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [selection]);

  return (
    <div className="flex flex-col h-full bg-slate-900/50">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-4 py-2 bg-slate-800/80 border-b border-white/5 shadow-sm z-10 shrink-0">
        <div className="flex items-center gap-2">
          <button
            disabled={currentPage <= 1}
            onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
            className="p-1.5 rounded bg-white/5 hover:bg-white/10 disabled:opacity-30 transition-colors"
          >
            ◀
          </button>
          <span className="text-sm text-slate-300 font-mono w-32 text-center flex items-center justify-center gap-1">
            第
            <input
              type="number"
              min={1}
              max={numPages || 1}
              value={currentPage}
              onChange={(e) => {
                const val = e.target.value;
                if (val === '') {
                  // 允许清空输入框
                  setCurrentPage('');
                  return;
                }
                const parsed = parseInt(val, 10);
                if (!isNaN(parsed)) {
                  // 这里我们暂时不限制最大值，让用户能输入完整的数字，或者在输入完成后才限制
                  setCurrentPage(parsed);
                }
              }}
              onBlur={() => {
                if (currentPage === '' || currentPage < 1) setCurrentPage(1);
                if (numPages && currentPage > numPages) setCurrentPage(numPages);
              }}
              className="w-10 bg-slate-900 border border-white/10 text-center rounded py-0.5 focus:outline-none focus:border-blue-500/50 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
            / {numPages || '-'} 页
          </span>
          <button
            disabled={numPages && currentPage >= numPages}
            onClick={() => setCurrentPage(p => Math.min(numPages, p + 1))}
            className="p-1.5 rounded bg-white/5 hover:bg-white/10 disabled:opacity-30 transition-colors"
          >
            ▶
          </button>
        </div>

        <div className="flex items-center gap-2">
          <button onClick={() => setScale(s => Math.max(0.5, s - 0.25))} className="px-2 py-1 text-xs rounded bg-white/5 hover:bg-white/10 text-slate-300">-</button>
          <span className="text-xs text-slate-400 font-mono w-12 text-center">{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale(s => Math.min(3.0, s + 0.25))} className="px-2 py-1 text-xs rounded bg-white/5 hover:bg-white/10 text-slate-300">+</button>
        </div>
      </div>

      {/* PDF 渲染区 */}
      <div className="flex-1 overflow-hidden relative flex">
        {/* PDF 主区域 */}
        <div
          className="flex-1 overflow-auto relative flex justify-center p-4"
          ref={containerRef}
          onMouseUp={handleMouseUp}
        >
        {!pdfUrl ? (
          <div className="flex items-center justify-center h-full text-slate-500">
            暂无 PDF 文件
          </div>
        ) : (
          <Document
            file={pdfUrl}
            onLoadSuccess={onDocumentLoadSuccess}
            loading={
              <div className="flex flex-col items-center justify-center h-full text-slate-500">
                <svg className="w-8 h-8 animate-spin mb-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span>正在加载 PDF...</span>
              </div>
            }
            error={
              <div className="text-red-400 p-4 bg-red-500/10 rounded border border-red-500/20">
                加载 PDF 失败，请检查文件是否存在
              </div>
            }
            className="shadow-2xl"
          >
            <Page
              key={`${currentPage}_${highlightText}`}
              pageNumber={currentPage || 1}
              scale={scale}
              customTextRenderer={customTextRenderer}
              loading={<div className="h-[800px] w-[600px] bg-slate-800 animate-pulse rounded" />}
              className="bg-white"
            />


          </Document>
        )}

        {/* 手动绑定弹窗 */}
        {selection && (
          <div
            className="bind-popup absolute z-50 bg-slate-800 border border-slate-700 shadow-xl rounded-lg p-2 flex items-center gap-2 animate-fade-in-up"
            style={{
              left: `${selection.x}px`,
              top: `${selection.y}px`,
              transform: 'translateX(-50%)'
            }}
          >
            <span className="text-xs text-slate-400 max-w-[150px] truncate" title={selection.text}>
              {selection.text}
            </span>
            <div className="w-px h-4 bg-slate-600"></div>
            <button
              onClick={handleBindClick}
              className="px-3 py-1 text-xs rounded bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 border border-blue-500/30 transition-colors whitespace-nowrap flex items-center gap-1"
            >
              📌 绑定到当前指标
            </button>
          </div>
        )}
        </div>

        {/* 右侧文本面板 */}
        {showTextPanel && (
          <div className="w-80 border-l border-white/10 bg-slate-900/80 flex flex-col shrink-0">
            {/* 面板标题 */}
            <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
              <span className="text-xs text-slate-400 font-medium">
                📝 识别文本
              </span>
              <button
                onClick={() => setShowTextPanel(false)}
                className="text-slate-500 hover:text-slate-300 text-xs"
              >
                ✕
              </button>
            </div>
            
            {/* 文本内容 */}
            <div className="flex-1 overflow-auto p-3">
              {extractedText ? (
                <div 
                  className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap font-mono break-all"
                  dangerouslySetInnerHTML={{
                    __html: highlightText 
                      ? extractedText.replace(
                          new RegExp(String(highlightText).replace(/[\s\u3000]/g, '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'),
                          match => `<span class="text-panel-highlight">${match}</span>`
                        )
                      : extractedText
                  }}
                />
              ) : (
                <div className="text-xs text-slate-600 text-center mt-10">
                  加载中...
                </div>
              )}
            </div>
            
            {/* 底部信息 */}
            <div className="px-3 py-1.5 border-t border-white/5 text-xs text-slate-600">
              {extractedText.length} 字符
              {highlightText && (
                <span className="ml-2">
                  | 高亮: "{highlightText}"
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 折叠按钮（面板关闭时显示） */}
      {!showTextPanel && (
        <button
          onClick={() => setShowTextPanel(true)}
          className="absolute right-2 top-12 z-10 px-2 py-1 text-xs rounded bg-slate-800/80 text-slate-400 hover:text-slate-200 border border-white/10"
          title="显示识别文本"
        >
          📝
        </button>
      )}
    </div>
  );
}
