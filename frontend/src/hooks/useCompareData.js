/**
 * useCompareData Hook
 *
 * 核心状态管理：指标列表、选中状态、匹配数据、进度追踪。
 * 所有组件共享此 Hook 返回的状态和方法。
 */

import { useState, useCallback } from 'react';
import {
  uploadFiles as apiUpload,
  runAnalysis as apiAnalyze,
  getMatches as apiGetMatches,
  updateStatus as apiUpdateStatus,
  exportReport as apiExport,
  exportOriginalReport as apiExportOriginal,
  exportText as apiExportText,
  saveManualBinding as apiSaveManualBinding,
} from '../api';
import fileMap from '../data/map.json';

const normalize = (str) => str.replace(/\s+/g, '');

const findMatchInSource = (sourceFile, yearStr) => {
  if (!sourceFile) return null;
  
  const yearMatch = yearStr?.match(/(\d{4})/);
  const year = yearMatch ? yearMatch[1] : null;
  
  if (!year || !fileMap[year]) return null;

  const sourceLines = sourceFile
    .split(/[;\n]+/)
    .map(s => s.trim())
    .filter(s => s);

  for (const line of sourceLines) {
    if (!line.includes('+P')) continue;
    
    if (line.toLowerCase().includes('http') || line.toLowerCase().includes('www') || line.includes('年鉴')) continue;

    const parts = line.split('+P');
    const rawName = parts[0];
    const pageMatch = parts[1].match(/(\d+)/);
    const page = pageMatch ? parseInt(pageMatch[1], 10) : null;
    
    if (page === null) continue;

    const normalizedRawName = normalize(rawName);

    for (const coreName of fileMap[year]) {
      const normalizedCoreName = normalize(coreName);
      if (normalizedRawName.includes(normalizedCoreName) || normalizedCoreName.includes(normalizedRawName)) {
        return { coreName, page, fullLine: line };
      }
    }
  }
  return null;
};

export function useCompareData() {
  const [triggerKey, setTriggerKey] = useState('');
  // ---- 状态 ----
  const [indicators, setIndicators] = useState([]);
  const [pdfNames, setPdfNames] = useState([]);
  const [allResults, setAllResults] = useState([]);     // IndicatorResult[]
  const [selectedId, setSelectedId] = useState(null);
  const [currentMatches, setCurrentMatches] = useState(null); // 当前选中的 IndicatorResult
  
  // PDF 阅读器相关状态
  const [selectedPdf, setSelectedPdf] = useState(null);
  const [targetPage, setTargetPage] = useState(1);
  const [highlightText, setHighlightText] = useState('');
  const [progress, setProgress] = useState({ total: 0, confirmed: 0, disputed: 0, not_found: 0, unchecked: 0 });

  const [loading, setLoading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState(null);
  const [uploaded, setUploaded] = useState(false);
  const [analyzed, setAnalyzed] = useState(false);

  const [availableColors, setAvailableColors] = useState([]);
  const [selectedColors, setSelectedColors] = useState([]);

  // ---- 上传文件 ----
  const upload = useCallback(async (excelFile, pdfFiles) => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiUpload(excelFile, pdfFiles);
      setUploaded(true);
      setAnalyzed(false);
      setIndicators([]);
      setAllResults([]);
      setSelectedId(null);
      setCurrentMatches(null);
      setSelectedPdf(null);
      setTargetPage(1);
      setHighlightText('');

      // 设置可用颜色，并默认全选
      const colors = data.colors || [];
      setAvailableColors(colors);
      setSelectedColors(colors);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // ---- 执行分析 ----
  const analyze = useCallback(async () => {
    if (selectedColors.length === 0) {
      setError('请至少选择一种颜色进行分析');
      return;
    }

    setAnalyzing(true);
    setError(null);
    try {
      const data = await apiAnalyze(selectedColors);
      setIndicators(data.indicators);
      setPdfNames(data.pdf_names);
      setAllResults(data.results);
      setProgress(data.progress);
      setAnalyzed(true);

      // 默认选中第一个
      if (data.results.length > 0) {
        setSelectedId(data.results[0].indicator.id);
        setCurrentMatches(data.results[0]);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setAnalyzing(false);
    }
  }, [selectedColors]);

// ---- 选中指标时加载匹配数据 ----
const selectIndicator = useCallback((id) => {
    setSelectedId(id);
    const found = allResults.find(r => r.indicator.id === id);
    setCurrentMatches(found || null);

    console.log('=== selectIndicator Debug ===');
    console.log('selected id:', id);
    console.log('source_file raw:', found?.indicator?.source_file);
    console.log('pdfNames:', pdfNames);
    console.log('best_matches:', found?.best_matches);

    if (found) {
      const sourceFile = found.indicator.source_file || '';
      const year = found.indicator.year || '';

      // 同时处理分号和换行符分隔
      const sourceLines = sourceFile
        .split(/[;\n]+/)
        .map(s => s.trim())
        .filter(s => s);

      console.log('source_lines:', sourceLines);

      let matchedPdfName = null;
      let parsedPage = null;

      // 1. 尝试使用映射表精确匹配
      const match = findMatchInSource(sourceFile, year);

      if (match) {
        console.log('Exact match found:', match);
        const ocrName = match.coreName + '_ocr.pdf';
        const transName = match.coreName + '_trans.pdf';

        if (pdfNames.includes(ocrName)) {
          matchedPdfName = ocrName;
        } else if (pdfNames.includes(transName)) {
          matchedPdfName = transName;
        } else {
          matchedPdfName = ocrName; // 默认回退到ocr
        }

        parsedPage = match.page;
      } else {
        console.log('No exact match, falling back to old logic');

        // 智能匹配：找到 best_matches 中实际有匹配值的那一行
        let targetSourceLine = null;

        // 策略1：从 best_matches 中找到真正有匹配的 PDF
        const bestMatches = found.best_matches || {};
        const matchedPdfs = Object.entries(bestMatches)
          .filter(([, m]) => m && m.is_match)
          .sort((a, b) => b[1].confidence - a[1].confidence);

        console.log('matched pdfs:', matchedPdfs);

        if (matchedPdfs.length > 0) {
          // 使用置信度最高的匹配
          const [bestPdfName, bestMatch] = matchedPdfs[0];
          matchedPdfName = bestPdfName;
          parsedPage = bestMatch.page_number;

          // 在 sourceLines 中找到与这个 PDF 名称匹配的行
          for (const line of sourceLines) {
            const cleanLine = line.split('+P')[0].trim();
            const cleanPdfName = bestPdfName.replace('_ocr.pdf', '');

            if (cleanPdfName.includes(cleanLine) || cleanLine.includes(cleanPdfName)) {
              targetSourceLine = line;
              console.log('matched source line by pdf:', targetSourceLine);
              break;
            }
          }

          // 如果按名称没匹配到，尝试按年份匹配
          if (!targetSourceLine) {
            const yearMatch = bestPdfName.match(/(\d{4})/);
            if (yearMatch) {
              const y = yearMatch[1];
              targetSourceLine = sourceLines.find(line => line.includes(y));
              console.log('matched source line by year:', targetSourceLine);
            }
          }
        }

        // 回退策略：如果还没有匹配到，使用第一个 source line 和第一个 PDF
        if (!targetSourceLine && sourceLines.length > 0) {
          targetSourceLine = sourceLines[0];
          console.log('fallback to first source line:', targetSourceLine);
        }

        if (!matchedPdfName && pdfNames.length > 0) {
          matchedPdfName = pdfNames[0];
          console.log('fallback to first pdf:', matchedPdfName);
        }

        // 从匹配的行中提取页码
        if (parsedPage === null && targetSourceLine) {
          const pageMatch = targetSourceLine.match(/\+P(\d+)/i);
          parsedPage = pageMatch ? parseInt(pageMatch[1], 10) : null;
          console.log('parsed page from source:', parsedPage);
        }
        
        // 如果前面没解析出页码，使用最佳匹配的页码
        if (parsedPage === null && matchedPdfs.length > 0) {
          parsedPage = matchedPdfs[0][1].page_number;
          console.log('using best match page:', parsedPage);
        }
      }

      // 设置选中的 PDF
      setSelectedPdf(matchedPdfName);

      // 设置页码和高亮文本
      if (parsedPage !== null) {
        setTargetPage(parsedPage);
      }

      // 检查是否有匹配的高亮文本
      const currentPdfMatch = matchedPdfName && found.best_matches ? found.best_matches[matchedPdfName] : null;

      if (currentPdfMatch && currentPdfMatch.is_match) {
        setHighlightText(currentPdfMatch.matched_value_raw || '');
        console.log('highlight from current pdf:', currentPdfMatch.matched_value_raw);
      } else {
        // 即使在当前 PDF 没找到，也先清空高亮，等 PDF 加载后再查找
        setHighlightText('');
        console.log('no match in current pdf, clearing highlight');
      }
      
      setTriggerKey(`${id}_${parsedPage || 1}`);
    }
  }, [allResults, pdfNames]);

  const selectNextIndicator = useCallback(() => {
    if (selectedId === null || selectedId === undefined || allResults.length === 0) return;
    const currentIndex = allResults.findIndex(r => r.indicator.id === selectedId);
    if (currentIndex >= 0 && currentIndex < allResults.length - 1) {
      selectIndicator(allResults[currentIndex + 1].indicator.id);
    }
  }, [selectedId, allResults, selectIndicator]);

  const selectPrevIndicator = useCallback(() => {
    if (selectedId === null || selectedId === undefined || allResults.length === 0) return;
    const currentIndex = allResults.findIndex(r => r.indicator.id === selectedId);
    if (currentIndex > 0) {
      selectIndicator(allResults[currentIndex - 1].indicator.id);
    }
  }, [selectedId, allResults, selectIndicator]);

  // ---- 更新核对状态 ----
  const setReviewStatus = useCallback(async (indicatorId, status, note = '') => {
    try {
      const data = await apiUpdateStatus(indicatorId, status, note);
      setProgress(data.progress);

      // 更新本地状态
      setIndicators(prev => prev.map(ind =>
        ind.id === indicatorId ? { ...ind, review_status: status, note } : ind
      ));
      setAllResults(prev => prev.map(r =>
        r.indicator.id === indicatorId
          ? { ...r, indicator: { ...r.indicator, review_status: status, note } }
          : r
      ));
    } catch (e) {
      setError(e.message);
    }
  }, []);

  // ---- 手动绑定 ----
  const handleManualBind = useCallback(async (text, page) => {
    if (!selectedId || !selectedPdf) return;
    
    try {
      const data = await apiSaveManualBinding(selectedId, selectedPdf, page, text);
      setProgress(data.progress);
      
      // 更新本地状态
      const status = '已确认';
      const note = `手动绑定: ${selectedPdf} (第 ${page} 页)`;
      
      setIndicators(prev => prev.map(ind =>
        ind.id === selectedId ? { ...ind, review_status: status, note } : ind
      ));
      
      setAllResults(prev => prev.map(r => {
        if (r.indicator.id === selectedId) {
          const newIndicator = { ...r.indicator, review_status: status, note };
          const manualMatch = {
            pdf_name: selectedPdf,
            page_number: page,
            matched_value_raw: text,
            confidence: 100.0,
            is_match: true,
            context: text,
            context_highlighted: `<mark>${text}</mark>`
          };
          const newBestMatches = { ...r.best_matches, [selectedPdf]: manualMatch };
          const newMatchesList = [...(r.matches[selectedPdf] || []), manualMatch];
          return { ...r, indicator: newIndicator, best_matches: newBestMatches, matches: { ...r.matches, [selectedPdf]: newMatchesList } };
        }
        return r;
      }));
      
      // 重新选择一次触发面板更新
      selectIndicator(selectedId);
      
    } catch (e) {
      setError(e.message);
    }
  }, [selectedId, selectedPdf, selectIndicator]);

  // ---- 导出报告 ----
  const doExport = useCallback(async () => {
    setError(null);
    try {
      await apiExport();
    } catch (e) {
      setError(e.message);
    }
  }, []);

  // ---- 导出标色原表 ----
  const doExportOriginal = useCallback(async () => {
    setError(null);
    try {
      await apiExportOriginal();
    } catch (e) {
      setError(e.message);
    }
  }, []);

  // ---- 导出PDF提取纯文本 ----
  const doExportText = useCallback(async () => {
    setError(null);
    try {
      await apiExportText();
    } catch (e) {
      setError(e.message);
    }
  }, []);

  // ---- 切换选中颜色 ----
  const toggleColor = useCallback((color) => {
    setSelectedColors(prev =>
      prev.includes(color)
        ? prev.filter(c => c !== color)
        : [...prev, color]
    );
  }, []);

  return {
    // 状态
    indicators, pdfNames, allResults,
    selectedId, currentMatches,
    progress,
    loading, analyzing, error,
    uploaded, analyzed,
    availableColors, selectedColors,
    selectedPdf, targetPage, highlightText, triggerKey,
    // 方法
    upload, analyze,
    selectIndicator, selectNextIndicator, selectPrevIndicator, setReviewStatus, handleManualBind,
    setSelectedPdf, setTargetPage,
    doExport, doExportOriginal, doExportText,
    setError, toggleColor,
  };
}
