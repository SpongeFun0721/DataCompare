/**
 * useCompareData Hook
 *
 * 核心状态管理：指标列表、选中状态、匹配数据、进度追踪。
 * 所有组件共享此 Hook 返回的状态和方法。
 */

import { useState, useCallback, useRef } from 'react';
import {
  uploadFiles as apiUpload,
  runAnalysis as apiAnalyze,
  getMatches as apiGetMatches,
  updateStatus as apiUpdateStatus,
  exportReport as apiExport,
  exportOriginalReport as apiExportOriginal,
  saveManualBinding as apiSaveManualBinding,
} from '../api';

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
  const [progress, setProgress] = useState({ total: 0, confirmed: 0, unchecked: 0 });

  const [loading, setLoading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState(null);
  const [uploaded, setUploaded] = useState(false);
  const [analyzed, setAnalyzed] = useState(false);

  const [availableColors, setAvailableColors] = useState([]);
  const [selectedColors, setSelectedColors] = useState([]);
  const [colorMapping, setColorMapping] = useState({}); // { "#XXXXXX": "yearbook" | "report" | "url" }
  const [matchedPdfs, setMatchedPdfs] = useState([]); // 后端匹配到的 PDF 列表

  // 保存批注的版本号，用于防止快速连续点击导致的竞态
  const saveVersionRef = useRef(0);

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
      // 重置颜色映射，根据色相判断：
      // 绿色 → yearbook（年鉴）、蓝色 → report（司局）、红色 → url（AI）
      const defaultMapping = {};
      for (const color of colors) {
        if (color === '无填充') continue;
        // 解析 #RRGGBB 判断主色调
        const hex = color.replace('#', '');
        const r = parseInt(hex.substring(0, 2), 16);
        const g = parseInt(hex.substring(2, 4), 16);
        const b = parseInt(hex.substring(4, 6), 16);
        
        // 绿色：G 最高且明显大于 R 和 B
        if (g > r && g > b && g - Math.max(r, b) > 30) {
          defaultMapping[color] = 'yearbook';
        }
        // 蓝色：B 最高且明显大于 R 和 G
        else if (b > r && b > g && b - Math.max(r, g) > 30) {
          defaultMapping[color] = 'report';
        }
        // 红色：R 最高且明显大于 G 和 B
        else if (r > g && r > b && r - Math.max(g, b) > 30) {
          defaultMapping[color] = 'url';
        }
        // 其他颜色（如灰色、黄色等）默认 AI
        else {
          defaultMapping[color] = 'url';
        }
      }
      setColorMapping(defaultMapping);
      // 保存后端匹配到的 PDF 列表
      setMatchedPdfs(data.matched_pdfs || []);
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
      const data = await apiAnalyze(selectedColors, colorMapping);
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
  }, [selectedColors, colorMapping]);

// ---- 选中指标时加载匹配数据 ----
// 核心原则：
// 1. PDF 名称和页码始终使用 indicator.matched_* 字段（来自 AI 数据来源行）
// 2. 高亮文本优先使用 bestMatch 的匹配值，无匹配时使用 target_value
// 3. 无论是否匹配到数值，都按来源行指定的页码打开 PDF
const selectIndicator = useCallback((id) => {
    setSelectedId(id);
    const found = allResults.find(r => r.indicator.id === id);
    setCurrentMatches(found || null);

    console.log('=== selectIndicator Debug (v3 - 始终使用来源行页码) ===');
    console.log('selected id:', id);
    console.log('best_matches:', found?.best_matches);
    console.log('indicator.matched_*:', {
      matched_source_type: found?.indicator?.matched_source_type,
      matched_pdf_name: found?.indicator?.matched_pdf_name,
      matched_page: found?.indicator?.matched_page,
    });

    if (!found) return;

    const bestMatches = found.best_matches || {};
    const indicatorData = found.indicator || {};

    // 1. 从 best_matches 中找到最佳匹配（仅用于高亮文本）
    let bestMatch = null;
    let bestConfidence = -1;

    for (const [pdfName, match] of Object.entries(bestMatches)) {
      if (!match) continue;
      if (match.is_match && match.confidence > bestConfidence) {
        bestMatch = { ...match, pdf_name: pdfName };
        bestConfidence = match.confidence;
      }
    }

    if (!bestMatch) {
      for (const [pdfName, match] of Object.entries(bestMatches)) {
        if (!match) continue;
        if (match.confidence > bestConfidence) {
          bestMatch = { ...match, pdf_name: pdfName };
          bestConfidence = match.confidence;
        }
      }
    }

    // 2. 获取来源行信息（始终可用）
    const matchedPdfName = indicatorData.matched_pdf_name;
    const matchedPage = indicatorData.matched_page;
    const matchedSourceType = indicatorData.matched_source_type;

    // 3. 获取当前指标关联的所有 PDF（从 best_matches 中获取）
    const bestMatchKeys = Object.keys(bestMatches);
    // 过滤掉以 core_name 为 key 的项（那些是未匹配到实际文件的）
    // 保留所有以 .pdf 结尾的 key，以及在 pdfNames 中存在的 key
    const allRelatedPdfs = bestMatchKeys.filter(key => 
      key.endsWith('.pdf') || pdfNames.includes(key)
    );
    // 如果过滤后为空，但 bestMatchKeys 不为空，说明所有 key 都是 core_name
    // 此时使用 bestMatchKeys 作为关联列表
    const relatedPdfs = allRelatedPdfs.length > 0 ? allRelatedPdfs : bestMatchKeys;

    // 4. 设置 PDF 阅读器
    if (matchedSourceType === 'url') {
      // URL 来源 → 不打开 PDF
      setSelectedPdf(null);
      setTargetPage(1);
      setHighlightText(String(indicatorData.target_value));
      console.log('⏭️ URL 来源，不打开 PDF');
    } else if (matchedPdfName && relatedPdfs.includes(matchedPdfName)) {
      // 有来源行指定的 PDF 且在关联列表中 → 按来源行页码打开
      setSelectedPdf(matchedPdfName);
      setTargetPage(matchedPage || 1);

      // 高亮文本：有匹配用匹配值，无匹配用 target_value
      if (bestMatch) {
        const highlightVal = bestMatch.matched_value_raw
          ?? String(bestMatch.matched_value)
          ?? String(indicatorData.target_value);
        setHighlightText(highlightVal);
        console.log(`✅ 有匹配: pdf=${matchedPdfName}, page=${matchedPage}, highlight="${highlightVal}"`);
      } else {
        setHighlightText(String(indicatorData.target_value));
        console.log(`⚠️ 无数值匹配: pdf=${matchedPdfName}, page=${matchedPage}, highlight=target_value="${indicatorData.target_value}"`);
      }
    } else if (relatedPdfs.length > 0) {
      // 有来源行指定的 PDF 但不在关联列表中，或没有来源行信息
      // 选择第一个关联的 PDF
      const firstPdf = relatedPdfs[0];
      setSelectedPdf(firstPdf);
      // 从 best_matches 中获取该 PDF 的页码
      const firstMatch = bestMatches[firstPdf];
      const firstPage = firstMatch?.page_number || matchedPage || 1;
      setTargetPage(firstPage);

      // 高亮文本
      if (bestMatch) {
        const highlightVal = bestMatch.matched_value_raw
          ?? String(bestMatch.matched_value)
          ?? String(indicatorData.target_value);
        setHighlightText(highlightVal);
        console.log(`✅ 有匹配（首个PDF）: pdf=${firstPdf}, page=${firstPage}, highlight="${highlightVal}"`);
      } else {
        setHighlightText(String(indicatorData.target_value));
        console.log(`⚠️ 无数值匹配（首个PDF）: pdf=${firstPdf}, page=${firstPage}, highlight=target_value="${indicatorData.target_value}"`);
      }
    } else {
      // 完全没有任何来源信息
      setSelectedPdf(null);
      setTargetPage(1);
      setHighlightText('');
      console.log('❌ 无任何来源信息');
    }

    setTriggerKey(`${id}_${matchedPage || 1}`);
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
      const status = '已核对';
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

  // ---- 保存批注 ----
  const saveComment = useCallback(async (indicatorId, comment) => {
    // 递增版本号，用于防止快速连续点击导致的竞态
    saveVersionRef.current += 1;
    const version = saveVersionRef.current;

    const indicator = indicators.find(ind => ind.id === indicatorId);
    if (!indicator) return;
    await setReviewStatus(indicatorId, indicator.review_status || '未核对', comment);

    // 如果版本号不匹配，说明有更新的请求已发出，丢弃当前结果
    if (version !== saveVersionRef.current) return;

    // 直接更新 currentMatches，避免 selectIndicator 读取旧的 allResults
    if (selectedId === indicatorId) {
      setCurrentMatches(prev => {
        if (!prev || prev.indicator.id !== indicatorId) return prev;
        return {
          ...prev,
          indicator: {
            ...prev.indicator,
            note: comment,
            review_status: indicator.review_status || '未核对',
          },
        };
      });
    }
  }, [indicators, setReviewStatus, selectedId, setCurrentMatches]);

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
    availableColors, selectedColors, colorMapping, matchedPdfs,
    selectedPdf, targetPage, highlightText, triggerKey,
    // 方法
    upload, analyze,
    selectIndicator, selectNextIndicator, selectPrevIndicator, setReviewStatus, handleManualBind,
    setSelectedPdf, setTargetPage,
    doExport, doExportOriginal, saveComment,
    setError, toggleColor, setColorMapping,
  };
}
