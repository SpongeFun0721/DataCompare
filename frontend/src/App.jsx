/**
 * App.jsx - 主应用组件
 *
 * 整体布局：
 * ┌──────────────────────────────────────────────┐
 * │  顶部导航栏（标题 + 上传 + 分析按钮）          │
 * ├──────────────┬───────────────────────────────┤
 * │ IndicatorTab │  MatchPanel                   │
 * │ (左 35%)     │  (右 65%)                     │
 * ├──────────────┴───────────────────────────────┤
 * │  StatusBar                                    │
 * └──────────────────────────────────────────────┘
 */

import { useRef, useState, useCallback } from 'react';
import { useCompareData } from './hooks/useCompareData';
import { useKeyboardShortcuts } from './hooks/useKeyboardShortcuts';
import IndicatorTable from './components/IndicatorTable';
import MatchPanel from './components/MatchPanel';
import StatusBar from './components/StatusBar';
import ShortcutsPanel from './components/ShortcutsPanel';
import Toast from './components/Toast';

function App() {
  const {
    indicators, pdfNames, allResults,
    selectedId, currentMatches,
    progress,
    loading, analyzing, error,
    uploaded, analyzed,
    availableColors, selectedColors, colorMapping,
    selectedPdf, targetPage, highlightText, triggerKey,
    upload, analyze,
    selectIndicator, selectNextIndicator, selectPrevIndicator, setReviewStatus, handleManualBind,
        setSelectedPdf, setTargetPage,
    doExport, doExportOriginal, doExportText, setError, toggleColor, setColorMapping,
  } = useCompareData();

    const excelInputRef = useRef(null);
  const pdfInputRef = useRef(null);

  const [excelFile, setExcelFile] = useState(null);
  const [pdfFiles, setPdfFiles] = useState([]);

    // ---- 年份筛选状态 ----
  const [yearFilter, setYearFilter] = useState('全部');
  const handleYearFilter = useCallback((year) => {
    setYearFilter(year);
  }, []);

  // ---- PDF 页面控制状态（供快捷键 PgUp/PgDn） ----
  const [pdfCurrentPage, setPdfCurrentPage] = useState(targetPage || 1);
  const [pdfNumPages, setPdfNumPages] = useState(null);
  const handlePdfPageChange = useCallback((page) => {
    setPdfCurrentPage(page);
  }, []);
  const handlePdfNumPagesChange = useCallback((num) => {
    setPdfNumPages(num);
  }, []);

  // ---- 撤销功能 ----
  const handleUndo = useCallback((action) => {
    if (action.type === 'review_status' && action.indicatorId) {
      setReviewStatus(action.indicatorId, '未核对');
    }
  }, [setReviewStatus]);

  // ---- 快捷键系统 ----
  const {
    showShortcutsPanel,
    setShowShortcutsPanel,
    toast,
    SHORTCUTS,
    pushUndo,
  } = useKeyboardShortcuts({
    selectedId,
    allResults,
    selectIndicator,
    selectNextIndicator,
    selectPrevIndicator,
    setReviewStatus: (id, status) => {
      if (status) {
        pushUndo({ type: 'review_status', indicatorId: id, status });
      }
      setReviewStatus(id, status);
    },
    onYearFilter: handleYearFilter,
    currentYearFilter: yearFilter,
    onExport: doExport,
    onUndo: handleUndo,
    currentPage: pdfCurrentPage,
    numPages: pdfNumPages,
    onPageChange: (page) => {
      setTargetPage(page);
    },
  });

  const handleUpload = async () => {
    if (!excelFile) {
      setError('请选择 Excel 文件');
      return;
    }
    await upload(excelFile, pdfFiles);
  };


  return (
    <div className="flex flex-col h-screen overflow-hidden">
      {/* ====== 顶部导航栏 ====== */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-white/5 bg-slate-900/80 backdrop-blur-xl z-20">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold shadow-lg shadow-blue-500/25">
            DC
          </div>
                    <div>
            <h1 className="text-base font-semibold text-white leading-tight">数据比对工具</h1>
            <p className="text-xs text-slate-500">Excel × PDF 年度报告数值核对</p>
          </div>
          {/* 快捷键提示按钮 */}
          <button
            onClick={() => setShowShortcutsPanel(true)}
            className="ml-2 px-2 py-1 text-xs rounded-lg bg-white/5 border border-white/10 text-slate-400 hover:text-slate-200 hover:bg-white/10 transition-all flex items-center gap-1"
            title="快捷键指南"
          >
            ⌨️ <kbd className="px-1 py-0.5 text-[10px] font-mono bg-slate-900 border border-slate-600 rounded">?</kbd>
          </button>
        </div>

        {/* 文件选择 & 操作按钮 */}
        <div className="flex items-center gap-3">
          {/* Excel 选择 */}
          <input
            ref={excelInputRef}
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            onChange={e => setExcelFile(e.target.files?.[0] || null)}
          />
          <button
            onClick={() => excelInputRef.current?.click()}
            className={`px-3 py-1.5 text-xs rounded-lg border transition-all ${
              excelFile
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                : 'bg-white/5 border-white/10 text-slate-300 hover:bg-white/10'
            }`}
          >
            📊 {excelFile ? excelFile.name : '选择 Excel'}
          </button>

          {/* PDF 选择 */}
          <input
            ref={pdfInputRef}
            type="file"
            accept=".pdf"
            multiple
            className="hidden"
            onChange={e => setPdfFiles(Array.from(e.target.files || []))}
          />
          <button
            onClick={() => pdfInputRef.current?.click()}
            className={`px-3 py-1.5 text-xs rounded-lg border transition-all ${
              pdfFiles.length > 0
                ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                : 'bg-white/5 border-white/10 text-slate-300 hover:bg-white/10'
            }`}
          >
            📄 {pdfFiles.length > 0 ? `${pdfFiles.length} 个 PDF` : '选择 PDF'}
          </button>

          {/* 上传按钮 */}
          <button
            onClick={handleUpload}
            disabled={loading || !excelFile}
            className="px-3 py-1.5 text-xs rounded-lg bg-white/5 border border-white/10 text-slate-300 hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            {loading ? '上传中...' : '📤 上传'}
          </button>


          {/* 分析按钮 */}
          <button
            onClick={analyze}
            disabled={analyzing || (!uploaded && indicators.length === 0)}
            className="px-4 py-1.5 text-xs rounded-lg bg-gradient-to-r from-blue-600 to-purple-600 hover:from-blue-500 hover:to-purple-500 text-white font-medium shadow-lg shadow-blue-500/20 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            {analyzing ? (
              <span className="flex items-center gap-1.5">
                <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                分析中...
              </span>
            ) : '🚀 开始分析'}
          </button>
        </div>
      </header>

      {/* ====== 错误提示 ====== */}
      {error && (
        <div className="mx-5 mt-3 px-4 py-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm flex items-center justify-between animate-fade-in-up">
          <span>⚠️ {error}</span>
          <button onClick={() => setError(null)} className="text-red-400/60 hover:text-red-400">✕</button>
        </div>
      )}

      {/* ====== 主内容区：左右分栏 ====== */}
      <main className="flex flex-1 overflow-hidden">
        {/* 左侧：指标表格 (35%) */}
        <aside className="w-[35%] min-w-[300px] border-r border-white/5 bg-slate-900/30 flex flex-col">
          {analyzed ? (
                        <IndicatorTable
              indicators={indicators}
              selectedId={selectedId}
              onSelect={selectIndicator}
              allResults={allResults}
              pdfNames={pdfNames}
              yearFilter={yearFilter}
            />
          ) : uploaded ? (
            <div className="flex-1 flex flex-col items-center justify-center p-6">
              <div className="text-center mb-6">
                <div className="text-4xl mb-3 opacity-80">🎨</div>
                <p className="text-slate-300 text-sm mb-1 font-medium">文件上传并解析成功</p>
                <p className="text-slate-500 text-xs">请选择要审查的单元格颜色（默认全选）</p>
              </div>
              <div className="flex flex-wrap gap-3 justify-center w-full">
                {availableColors.map(color => {
                  const isSelected = selectedColors.includes(color);
                  const isNoFill = color === '无填充';
                  const currentType = colorMapping[color] || 'url';
                  return (
                    <div
                      key={color}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-all ${
                        isSelected 
                          ? 'border-blue-500/50 bg-blue-500/10 shadow-[0_0_10px_rgba(59,130,246,0.1)]' 
                          : 'border-white/10 bg-white/5 opacity-50 hover:opacity-80'
                      }`}
                    >
                      <button
                        onClick={() => toggleColor(color)}
                        className="flex items-center gap-2"
                      >
                        <div
                          className="w-3.5 h-3.5 rounded shadow-sm border border-white/20"
                          style={{ backgroundColor: isNoFill ? 'transparent' : color }}
                        />
                        <span className={`text-xs font-mono ${isSelected ? 'text-blue-400 font-medium' : 'text-slate-400'}`}>
                          {isNoFill ? '无填充' : color}
                        </span>
                      </button>
                      {!isNoFill && (
                        <select
                          value={currentType}
                          onChange={(e) => {
                            setColorMapping(prev => ({
                              ...prev,
                              [color]: e.target.value,
                            }));
                          }}
                          className="text-xs bg-slate-800 border border-white/10 rounded px-1.5 py-0.5 text-slate-300 cursor-pointer hover:border-white/20 focus:outline-none focus:border-blue-500/50"
                        >
                          <option value="yearbook">年鉴</option>
                          <option value="report">司局</option>
                          <option value="url">AI</option>
                        </select>
                      )}
                    </div>
                  );
                })}
                {availableColors.length === 0 && (
                  <p className="text-xs text-slate-500">未检测到任何指标数据</p>
                )}
              </div>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center px-8">
                <div className="text-5xl mb-4 opacity-20">📋</div>
                <p className="text-slate-500 text-sm mb-1">尚未加载数据</p>
                <p className="text-slate-600 text-xs">请上传 Excel 和 PDF 文件后点击"开始分析"</p>
              </div>
            </div>
          )}
        </aside>

        {/* 右侧：匹配展示区 (65%) */}
        <section className="flex-1 flex flex-col bg-slate-900/10">
          <MatchPanel
            currentMatches={currentMatches}
            pdfNames={pdfNames}
            selectedPdf={selectedPdf}
            onPdfChange={setSelectedPdf}
            targetPage={targetPage}
            highlightText={highlightText}
            triggerKey={triggerKey}
            analyzing={analyzing}
                                                onConfirm={(id) => {
              pushUndo({ type: 'review_status', indicatorId: id, status: '已核对' });
              setReviewStatus(id, '已核对');
              // 自动跳转到下一个未核对指标
              setTimeout(() => {
                const currentIdx = allResults.findIndex(r => r.indicator.id === id);
                if (currentIdx < 0) return;
                // 先往后找未核对的
                for (let i = currentIdx + 1; i < allResults.length; i++) {
                  if (allResults[i].indicator.review_status === '未核对') {
                    selectIndicator(allResults[i].indicator.id);
                    return;
                  }
                }
                // 再从头找
                for (let i = 0; i < currentIdx; i++) {
                  if (allResults[i].indicator.review_status === '未核对') {
                    selectIndicator(allResults[i].indicator.id);
                    return;
                  }
                }
                // 全部已核对，就下一项
                selectNextIndicator();
              }, 150);
            }}
                                                onDispute={(id) => {
              pushUndo({ type: 'review_status', indicatorId: id, status: '未核对' });
              setReviewStatus(id, '未核对');
              setTimeout(() => {
                const currentIdx = allResults.findIndex(r => r.indicator.id === id);
                if (currentIdx < 0) return;
                for (let i = currentIdx + 1; i < allResults.length; i++) {
                  if (allResults[i].indicator.review_status === '未核对') {
                    selectIndicator(allResults[i].indicator.id);
                    return;
                  }
                }
                for (let i = 0; i < currentIdx; i++) {
                  if (allResults[i].indicator.review_status === '未核对') {
                    selectIndicator(allResults[i].indicator.id);
                    return;
                  }
                }
                selectNextIndicator();
              }, 150);
            }}
            onNext={selectNextIndicator}
            onPrev={selectPrevIndicator}
            onManualBind={handleManualBind}
            onPdfPageChange={handlePdfPageChange}
            onPdfNumPagesChange={handlePdfNumPagesChange}
          />
        </section>
      </main>

            {/* ====== 底部状态栏 ====== */}
      <StatusBar
        progress={progress}
        analyzed={analyzed}
        onExport={doExport}
        onExportOriginal={doExportOriginal}
        onExportText={doExportText}
      />

      {/* ====== 快捷键面板（? 打开） ====== */}
      <ShortcutsPanel
        isOpen={showShortcutsPanel}
        onClose={() => setShowShortcutsPanel(false)}
      />

      {/* ====== Toast 快捷键操作反馈 ====== */}
      <Toast toast={toast} />
    </div>
  );
}

export default App;
