import React from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import PdfStatusBar from './PdfStatusBar';
import { getPdfUrl } from '../api';

// 确保 worker 已配置（PdfViewer 中已配置，这里复用）
pdfjs.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjs.version}/build/pdf.worker.min.mjs`;

export default function MatchPanel({
  currentMatches,
  pdfNames,
  selectedPdf,
  onPdfChange,
  targetPage,
  highlightText,
  triggerKey,
  analyzing,
  onConfirm,
  onDispute,
  onNotFound,
  onNext,
  onPrev,
  onManualBind,
  onPdfPageChange,
  onPdfNumPagesChange,
}) {
  const indicator = currentMatches?.indicator;

  // 获取当前指标关联的所有 PDF 名称（来自 best_matches 和 matches）
  const relatedPdfNames = React.useMemo(() => {
    if (!currentMatches) return [];
    const bestMatchKeys = Object.keys(currentMatches.best_matches || {});
    const matchKeys = Object.keys(currentMatches.matches || {});
    const allKeys = new Set([...bestMatchKeys, ...matchKeys]);
    if (pdfNames && pdfNames.length > 0) {
      const ordered = pdfNames.filter(name => allKeys.has(name));
      const extra = Array.from(allKeys).filter(name => !pdfNames.includes(name));
      return [...ordered, ...extra];
    }
    return Array.from(allKeys);
  }, [currentMatches, pdfNames]);

  // 获取当前查看 PDF 的匹配信息
  const currentPdfMatchInfo = React.useMemo(() => {
    if (!currentMatches || !selectedPdf) return null;
    return currentMatches.best_matches?.[selectedPdf] || null;
  }, [currentMatches, selectedPdf]);

  React.useEffect(() => {
    console.log('🔍 MatchPanel 接收到的 props:');
    console.log('  - highlightText:', highlightText);
    console.log('  - targetPage:', targetPage);
    console.log('  - indicator:', indicator);
    console.log('  - currentPdfMatchInfo:', currentPdfMatchInfo);
    if (currentPdfMatchInfo) {
      console.log('  - 匹配值:', currentPdfMatchInfo.matched_value_raw);
      console.log('  - 上下文:', currentPdfMatchInfo.context);
    }
  }, [highlightText, targetPage, indicator, currentPdfMatchInfo]);

  // ============================================================
  // PDF 预加载策略：
  // - 司局 PDF（最多 4 个）：使用 fetch + Blob URL 预加载，切换时秒开
  // - 年鉴 PDF：按需加载（不预加载）
  // ============================================================
  const isYearbook = React.useCallback((name) => name.includes('年鉴'), []);

  // 需要预加载的司局 PDF（取前 4 个）
  const preloadPdfNames = React.useMemo(() => {
    if (!pdfNames) return [];
    return pdfNames.filter(name => !isYearbook(name)).slice(0, 4);
  }, [pdfNames, isYearbook]);

  // Blob URL 缓存：pdfName -> blobUrl
  const [pdfBlobCache, setPdfBlobCache] = React.useState({});

  // 预加载司局 PDF：用 fetch 下载数据并创建 Blob URL
  React.useEffect(() => {
    let cancelled = false;
    const cache = {};

    async function preloadAll() {
      for (const name of preloadPdfNames) {
        if (cancelled) return;
        try {
          const url = getPdfUrl(name);
          const resp = await fetch(url);
          if (!resp.ok) {
            console.warn(`⚠️ PDF 预加载失败: ${name} (HTTP ${resp.status})`);
            continue;
          }
          const blob = await resp.blob();
          const blobUrl = URL.createObjectURL(blob);
          cache[name] = blobUrl;
          console.log(`📦 PDF 预加载成功 (Blob URL): ${name} (${blob.size} bytes)`);
        } catch (err) {
          console.warn(`⚠️ PDF 预加载异常: ${name}`, err);
        }
      }
      if (!cancelled) {
        setPdfBlobCache(cache);
      }
    }

    preloadAll();

    return () => {
      cancelled = true;
      // 清理旧的 Blob URL
      Object.values(pdfBlobCache).forEach(url => URL.revokeObjectURL(url));
    };
  }, [preloadPdfNames]); // 注意：这里故意不依赖 pdfBlobCache，避免循环

  // 获取当前 PDF 的 URL（优先使用缓存的 Blob URL）
  const pdfUrl = React.useMemo(() => {
    if (!selectedPdf) return null;
    // 如果已缓存，使用 Blob URL（秒开）
    if (pdfBlobCache[selectedPdf]) return pdfBlobCache[selectedPdf];
    // 否则使用原始 URL（按需加载）
    return getPdfUrl(selectedPdf);
  }, [selectedPdf, pdfBlobCache]);

  // 判断当前指标是否为 AI（URL）来源
  const isUrlSource = React.useMemo(() => {
    if (!indicator) return false;
    return !!(indicator.source_file_url);
  }, [indicator]);

  // 从 indicator.source_pages 中提取所有 URL
  const urlSources = React.useMemo(() => {
    if (!indicator || !indicator.source_pages) return [];
    return indicator.source_pages.filter(sp => sp.source_type === 'url');
  }, [indicator]);

  // 当前选中的 URL 索引（根据指标年份自动匹配）
  const [selectedUrlIndex, setSelectedUrlIndex] = React.useState(0);

  // 根据指标年份自动选中对应的 URL
  React.useEffect(() => {
    if (urlSources.length === 0 || !indicator?.year) return;
    const indYearMatch = indicator.year.match(/(\d{4})/);
    if (!indYearMatch) return;
    const indYear = indYearMatch[1];
    // 查找 year_label 匹配的 URL
    const matchedIdx = urlSources.findIndex(sp => {
      if (!sp.year_label) return false;
      const spYearMatch = sp.year_label.match(/(\d{4})/);
      return spYearMatch && spYearMatch[1] === indYear;
    });
    if (matchedIdx >= 0 && matchedIdx !== selectedUrlIndex) {
      setSelectedUrlIndex(matchedIdx);
    }
  }, [urlSources, indicator?.year]);

  // 当前选中的 URL 来源
  const urlSource = React.useMemo(() => {
    if (urlSources.length === 0) return null;
    return urlSources[selectedUrlIndex] || urlSources[0];
  }, [urlSources, selectedUrlIndex]);

  // 加载中骨架屏
  if (analyzing) {
    return (
      <div className="flex flex-col h-full bg-slate-900/10">
        <div className="px-5 py-4 border-b border-white/5">
          <div className="skeleton h-6 w-48 mb-2" />
          <div className="skeleton h-4 w-32" />
        </div>
        <div className="flex-1 p-4 space-y-4">
          <div className="skeleton h-full w-full rounded-xl" />
        </div>
      </div>
    );
  }

  // 无数据引导
  if (!currentMatches) {
    return (
      <div className="flex flex-col h-full bg-slate-900/10">
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <div className="text-6xl mb-4 opacity-30">📊</div>
            <p className="text-slate-400 text-sm">选择左侧指标查看 PDF 对应内容</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full relative">
      {/* 顶部状态条 */}
      <PdfStatusBar
        indicator={indicator}
        matchInfo={currentPdfMatchInfo}
        onConfirm={onConfirm}
        onDispute={onDispute}
        onNotFound={onNotFound}
        onPrev={onPrev}
        onNext={onNext}
      />

      {/* 数值不一致警告 */}
      {currentPdfMatchInfo && indicator && 
       currentPdfMatchInfo.matched_value !== undefined &&
       currentPdfMatchInfo.matched_value !== null &&
       indicator.target_value !== undefined &&
       indicator.target_value !== null &&
       currentPdfMatchInfo.matched_value !== indicator.target_value && (
        <div className="px-4 py-2 bg-amber-500/10 border-b border-amber-500/20 flex items-center gap-2">
          <span className="text-amber-400 text-xs">⚠️</span>
          <span className="text-amber-300 text-xs">
            PDF中匹配的数值（{currentPdfMatchInfo.matched_value}）与Excel目标值（{indicator.target_value}）不一致，请人工核对
          </span>
        </div>
      )}

      {/* PDF 选择器 */}
      {relatedPdfNames.length > 0 && (
        <div className="px-4 py-2 bg-slate-800/50 border-b border-white/5 flex items-center gap-2">
          <span className="text-xs text-slate-500">查看报告:</span>
          <select
            value={selectedPdf || ''}
            onChange={(e) => onPdfChange(e.target.value)}
            className="bg-slate-900 border border-white/10 text-slate-300 text-xs rounded px-2 py-1 outline-none focus:border-blue-500/50 flex-1 max-w-[400px]"
          >
            {relatedPdfNames.map(name => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>
      )}

      {/* PDF 阅读器区域 - 使用 Blob URL 缓存加速 */}
      <div className="flex-1 min-h-0 relative">
        <div className="h-full">
          <PdfViewerWithPreload
            pdfUrl={pdfUrl}
            targetPage={targetPage}
            highlightText={highlightText}
            triggerKey={triggerKey}
            onTextSelect={onManualBind}
            onPageChange={onPdfPageChange}
            onNumPagesChange={onPdfNumPagesChange}
            isUrlSource={isUrlSource}
            urlSource={urlSource}
            urlSources={urlSources}
            selectedUrlIndex={selectedUrlIndex}
            onUrlIndexChange={setSelectedUrlIndex}
          />
        </div>
      </div>
    </div>
  );
}

/**
 * PDF 阅读器组件
 * - 使用 Blob URL 缓存加速（由父组件 MatchPanel 管理）
 * - 年鉴 PDF 按需加载（不预缓存）
 */
function PdfViewerWithPreload({
  pdfUrl,
  targetPage,
  highlightText,
  triggerKey,
  onTextSelect,
  onPageChange,
  onNumPagesChange,
  isUrlSource,
  urlSource,
  urlSources,
  selectedUrlIndex,
  onUrlIndexChange,
}) {
  const [numPages, setNumPages] = React.useState(null);
  const [currentPage, setCurrentPage] = React.useState(targetPage || 1);
  const [scale, setScale] = React.useState(1.0);
  const containerRef = React.useRef(null);
  const pdfDocRef = React.useRef(null);
  const [selection, setSelection] = React.useState(null);
  const [extractedText, setExtractedText] = React.useState('');
  const [showTextPanel, setShowTextPanel] = React.useState(true);
  const [loadError, setLoadError] = React.useState(null);

  // AI（URL）来源的状态
  const [webText, setWebText] = React.useState('');
  const [webTitle, setWebTitle] = React.useState('');
  const [webLoading, setWebLoading] = React.useState(false);
  const [webError, setWebError] = React.useState(null);

  // 提取页面文本
  const extractPageText = React.useCallback(async (pageNum) => {
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

  // triggerKey 变化时同步
  React.useEffect(() => {
    if (targetPage && targetPage !== currentPage) {
      setCurrentPage(targetPage);
    }
  }, [triggerKey]);

  // highlightText 变化时重新提取文本
  React.useEffect(() => {
    if (currentPage) extractPageText(currentPage);
  }, [highlightText, currentPage, extractPageText]);

  // 外部 targetPage 变化时同步
  React.useEffect(() => {
    if (targetPage && targetPage !== currentPage) {
      setCurrentPage(targetPage);
    }
  }, [targetPage]);

  // 通知父组件 numPages
  React.useEffect(() => {
    if (numPages && onNumPagesChange) onNumPagesChange(numPages);
  }, [numPages, onNumPagesChange]);

  // 通知父组件页面变化
  React.useEffect(() => {
    if (onPageChange) onPageChange(currentPage);
  }, [currentPage, onPageChange]);

  const onDocumentLoadSuccess = React.useCallback((pdf) => {
    pdfDocRef.current = pdf;
    setNumPages(pdf.numPages);
    console.log('✅ PDF 加载成功, 页数:', pdf.numPages);
    extractPageText(currentPage);
  }, [currentPage, extractPageText]);

  const onDocumentLoadError = React.useCallback((error) => {
    console.error('❌ PDF 加载失败:', error.message);
    setLoadError(error.message || '未知错误');
  }, []);

  // 高亮逻辑
  const customTextRenderer = React.useCallback(({ str, itemIndex }) => {
    const spacelessStr = str.replace(/[\s\u3000]/g, '');
    if (!highlightText) return spacelessStr;
    const highlightStr = String(highlightText).replace(/[\s\u3000]/g, '');
    if (!highlightStr) return spacelessStr;
    const numMatch = highlightStr.match(/[\d,]+\.?\d*/);
    if (!numMatch) {
      if (spacelessStr.includes(highlightStr)) {
        return spacelessStr.replace(
          new RegExp(highlightStr.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'),
          match => `<mark class="pdf-highlight-mark">${match}</mark>`
        );
      }
      return spacelessStr;
    }
    const num = numMatch[0].replace(/,/g, '');
    const regex = new RegExp(
      `(?<![0-9.,])${num.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[^0-9.,]*`, 'g'
    );
    return spacelessStr.replace(regex, match => {
      const valMatch = match.match(/[\d,]+\.?\d*/);
      if (valMatch && valMatch[0] === num) {
        return `<mark class="pdf-highlight-mark">${match}</mark>`;
      }
      return match;
    });
  }, [highlightText]);

  // 跨 span 高亮
  React.useEffect(() => {
    document.querySelectorAll('.react-pdf__Page__textContent span[role="presentation"]').forEach(el => {
      el.style.backgroundColor = '';
      el.style.fontWeight = '';
    });
    if (!highlightText) return;
    const target = String(highlightText).replace(/[\s\u3000]/g, '');
    if (!target) return;
    const HIGHLIGHT_STYLE = { backgroundColor: 'rgba(255, 255, 0, 0.4)', fontWeight: 'bold' };
    const numMatch = target.match(/[\d,]+\.?\d*/);
    const matchNum = numMatch ? numMatch[0].replace(/,/g, '') : null;
    const doHighlight = () => {
      const textContent = document.querySelector('.react-pdf__Page__textContent');
      if (!textContent) return false;
      const spans = Array.from(textContent.querySelectorAll('span[role="presentation"]'));
      if (spans.length === 0) return false;
      const getSpanText = (span) => span.textContent.replace(/[\s\u3000]/g, '');
      const isMatch = (text) => {
        if (text.includes(target)) return true;
        if (matchNum) {
          const regex = new RegExp(`(?<![0-9.,])${matchNum.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[^0-9.,]*`);
          return regex.test(text);
        }
        return false;
      };
      for (const span of spans) {
        if (isMatch(getSpanText(span))) {
          Object.assign(span.style, HIGHLIGHT_STYLE);
          return true;
        }
      }
      for (let i = 0; i < spans.length; i++) {
        let mergedText = getSpanText(spans[i]);
        if (!mergedText) continue;
        for (let j = i + 1; j < spans.length; j++) {
          mergedText += getSpanText(spans[j]);
          if (isMatch(mergedText)) {
            for (let k = i; k <= j; k++) Object.assign(spans[k].style, HIGHLIGHT_STYLE);
            return true;
          }
          if (mergedText.length > target.length + 30) break;
        }
      }
      return false;
    };
    if (doHighlight()) return;
    const textContent = document.querySelector('.react-pdf__Page__textContent');
    if (textContent) {
      const observer = new MutationObserver(() => { if (doHighlight()) observer.disconnect(); });
      observer.observe(textContent, { childList: true, subtree: true, characterData: true });
      const timeout = setTimeout(() => observer.disconnect(), 5000);
      return () => { observer.disconnect(); clearTimeout(timeout); };
    }
    const bodyObserver = new MutationObserver(() => {
      const tc = document.querySelector('.react-pdf__Page__textContent');
      if (tc) {
        if (doHighlight()) { bodyObserver.disconnect(); return; }
        const observer = new MutationObserver(() => { if (doHighlight()) observer.disconnect(); });
        observer.observe(tc, { childList: true, subtree: true, characterData: true });
        setTimeout(() => observer.disconnect(), 5000);
        bodyObserver.disconnect();
      }
    });
    bodyObserver.observe(document.body, { childList: true, subtree: true });
    const timeout = setTimeout(() => bodyObserver.disconnect(), 5000);
    return () => { bodyObserver.disconnect(); clearTimeout(timeout); };
  }, [highlightText, currentPage]);

  // 鼠标选中
  const handleMouseUp = React.useCallback(() => {
    const activeSelection = window.getSelection();
    if (!activeSelection || activeSelection.isCollapsed) { setSelection(null); return; }
    const anchorNode = activeSelection.anchorNode;
    if (!anchorNode || !anchorNode.parentElement?.closest('.react-pdf__Page')) { setSelection(null); return; }
    const text = activeSelection.toString().trim();
    if (text) {
      const range = activeSelection.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      const containerRect = containerRef.current.getBoundingClientRect();
      setSelection({ text, x: rect.left - containerRect.left + (rect.width / 2), y: rect.top - containerRect.top - 40 });
    }
  }, []);

  const handleBindClick = React.useCallback(() => {
    if (selection && onTextSelect) {
      onTextSelect(selection.text, currentPage);
      setSelection(null);
      window.getSelection().removeAllRanges();
    }
  }, [selection, onTextSelect, currentPage]);

  // 点击空白处取消选择
  React.useEffect(() => {
    const handleClickOutside = (e) => {
      if (selection && !e.target.closest('.bind-popup')) {
        setSelection(null);
        window.getSelection().removeAllRanges();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [selection]);

  // AI（URL）来源：通过后端代理获取网页纯文本
  React.useEffect(() => {
    if (!isUrlSource || !urlSource?.url) return;
    let cancelled = false;
    setWebLoading(true);
    setWebError(null);
    const proxyUrl = `/api/proxy-url?url=${encodeURIComponent(urlSource.url)}`;
    fetch(proxyUrl)
      .then(resp => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return resp.json();
      })
      .then(data => {
        if (!cancelled) {
          setWebText(data.text || '');
          setWebTitle(data.title || '');
          setWebLoading(false);
        }
      })
      .catch(err => {
        if (!cancelled) {
          setWebError(err.message);
          setWebLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, [isUrlSource, urlSource?.url]);

  // 高亮逻辑：在文本中查找 highlightText
  const renderHighlightedText = React.useCallback((text, highlight) => {
    if (!highlight) return text;
    const target = String(highlight).replace(/[\s\u3000]/g, '');
    if (!target) return text;
    const numMatch = target.match(/[\d,]+\.?\d*/);
    const matchNum = numMatch ? numMatch[0].replace(/,/g, '') : null;
    
    // 按行处理，每行独立高亮
    return text.split('\n').map((line, idx) => {
      const spacelessLine = line.replace(/[\s\u3000]/g, '');
      let shouldHighlight = false;
      if (spacelessLine.includes(target)) {
        shouldHighlight = true;
      } else if (matchNum) {
        const regex = new RegExp(`(?<![0-9.,])${matchNum.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[^0-9.,]*`);
        if (regex.test(spacelessLine)) shouldHighlight = true;
      }
      if (shouldHighlight) {
        return `<span class="text-panel-highlight">${line}</span>`;
      }
      return line;
    }).join('\n');
  }, []);

  // 如果是 AI（URL）来源，显示网页文本
  if (isUrlSource && urlSource?.url) {
    return (
      <div className="flex flex-col h-full bg-slate-900/50">
        {/* 顶部工具栏 */}
        <div className="flex items-center justify-between px-4 py-2 bg-slate-800/80 border-b border-white/5 shadow-sm z-10 shrink-0">
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">🌐 网页文本</span>
            {urlSources.length > 1 && (
              <select
                value={selectedUrlIndex}
                onChange={(e) => onUrlIndexChange(parseInt(e.target.value, 10))}
                className="bg-slate-900 border border-white/10 text-slate-300 text-xs rounded px-2 py-1 outline-none focus:border-blue-500/50 max-w-[200px]"
              >
                {urlSources.map((sp, idx) => (
                  <option key={idx} value={idx}>
                    {sp.year_label || `URL ${idx + 1}`}{sp.url ? `: ${sp.url.slice(0, 40)}...` : ''}
                  </option>
                ))}
              </select>
            )}
            {webTitle && <span className="text-xs text-slate-500 truncate max-w-[200px]">{webTitle}</span>}
          </div>
          <div className="flex items-center gap-2">
            <a
              href={urlSource.url}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2 py-1 text-xs rounded bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 border border-blue-500/30 transition-colors"
            >在新窗口打开 ↗</a>
          </div>
        </div>

        {/* 文本展示区 */}
        <div className="flex-1 overflow-hidden relative flex">
          <div className="flex-1 overflow-auto p-4">
            {webLoading ? (
              <div className="flex flex-col items-center justify-center h-full text-slate-500">
                <svg className="w-8 h-8 animate-spin mb-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span>正在加载网页内容...</span>
              </div>
            ) : webError ? (
              <div className="text-red-400 p-4 bg-red-500/10 rounded border border-red-500/20">
                加载失败: {webError}
              </div>
            ) : (
              <div
                className="text-xs text-slate-300 leading-relaxed whitespace-pre-wrap font-mono break-all"
                dangerouslySetInnerHTML={{
                  __html: renderHighlightedText(webText, highlightText)
                }}
              />
            )}
          </div>

          {/* 右侧信息面板 */}
          {showTextPanel && (
            <div className="w-80 border-l border-white/10 bg-slate-900/80 flex flex-col shrink-0">
              <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
                <span className="text-xs text-slate-400 font-medium">📝 信息面板</span>
                <button onClick={() => setShowTextPanel(false)} className="text-slate-500 hover:text-slate-300 text-xs">✕</button>
              </div>
              <div className="flex-1 overflow-auto p-3">
                <div className="text-xs text-slate-500">
                  <p className="mb-2">网页正文已提取纯文本</p>
                  <p className="text-slate-600">查找目标值:</p>
                  <p className="mt-1 font-mono text-amber-400">{highlightText}</p>
                  {webTitle && (
                    <>
                      <p className="mt-3 text-slate-600">页面标题:</p>
                      <p className="mt-1 text-slate-400 text-[11px]">{webTitle}</p>
                    </>
                  )}
                </div>
              </div>
              <div className="px-3 py-1.5 border-t border-white/5 text-xs text-slate-600 break-all">
                {webText.length} 字符 | {urlSource.url}
              </div>
            </div>
          )}
        </div>

        {/* 折叠按钮 */}
        {!showTextPanel && (
          <button
            onClick={() => setShowTextPanel(true)}
            className="absolute right-2 top-12 z-10 px-2 py-1 text-xs rounded bg-slate-800/80 text-slate-400 hover:text-slate-200 border border-white/10"
            title="显示信息面板"
          >📝</button>
        )}
      </div>
    );
  }


  return (
    <div className="flex flex-col h-full bg-slate-900/50">
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-4 py-2 bg-slate-800/80 border-b border-white/5 shadow-sm z-10 shrink-0">
        <div className="flex items-center gap-2">
          <button
            disabled={currentPage <= 1}
            onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
            className="p-1.5 rounded bg-white/5 hover:bg-white/10 disabled:opacity-30 transition-colors"
          >◀</button>
          <span className="text-sm text-slate-300 font-mono w-32 text-center flex items-center justify-center gap-1">
            第
            <input
              type="number"
              min={1}
              max={numPages || 1}
              value={currentPage}
              onChange={(e) => {
                const val = e.target.value;
                if (val === '') { setCurrentPage(''); return; }
                const parsed = parseInt(val, 10);
                if (!isNaN(parsed)) setCurrentPage(parsed);
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
          >▶</button>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setScale(s => Math.max(0.5, s - 0.25))} className="px-2 py-1 text-xs rounded bg-white/5 hover:bg-white/10 text-slate-300">-</button>
          <span className="text-xs text-slate-400 font-mono w-12 text-center">{Math.round(scale * 100)}%</span>
          <button onClick={() => setScale(s => Math.min(3.0, s + 0.25))} className="px-2 py-1 text-xs rounded bg-white/5 hover:bg-white/10 text-slate-300">+</button>
        </div>
      </div>

      {/* PDF 渲染区 */}
      <div className="flex-1 overflow-hidden relative flex">
        <div className="flex-1 overflow-auto relative flex justify-center p-4" ref={containerRef} onMouseUp={handleMouseUp}>
          {!pdfUrl ? (
            <div className="flex items-center justify-center h-full text-slate-500">暂无 PDF 文件</div>
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
              style={{ left: `${selection.x}px`, top: `${selection.y}px`, transform: 'translateX(-50%)' }}
            >
              <span className="text-xs text-slate-400 max-w-[150px] truncate" title={selection.text}>{selection.text}</span>
              <div className="w-px h-4 bg-slate-600"></div>
              <button
                onClick={handleBindClick}
                className="px-3 py-1 text-xs rounded bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 border border-blue-500/30 transition-colors whitespace-nowrap flex items-center gap-1"
              >📌 绑定到当前指标</button>
            </div>
          )}
        </div>

        {/* 右侧文本面板 */}
        {showTextPanel && (
          <div className="w-80 border-l border-white/10 bg-slate-900/80 flex flex-col shrink-0">
            <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
              <span className="text-xs text-slate-400 font-medium">📝 识别文本</span>
              <button onClick={() => setShowTextPanel(false)} className="text-slate-500 hover:text-slate-300 text-xs">✕</button>
            </div>
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
                <div className="text-xs text-slate-600 text-center mt-10">加载中...</div>
              )}
            </div>
            <div className="px-3 py-1.5 border-t border-white/5 text-xs text-slate-600">
              {extractedText.length} 字符
              {highlightText && <span className="ml-2">| 高亮: "{highlightText}"</span>}
            </div>
          </div>
        )}
      </div>

      {/* 折叠按钮 */}
      {!showTextPanel && (
        <button
          onClick={() => setShowTextPanel(true)}
          className="absolute right-2 top-12 z-10 px-2 py-1 text-xs rounded bg-slate-800/80 text-slate-400 hover:text-slate-200 border border-white/10"
          title="显示识别文本"
        >📝</button>
      )}
    </div>
  );
}
