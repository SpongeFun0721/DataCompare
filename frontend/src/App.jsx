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

import { useRef, useState } from 'react';
import { useCompareData } from './hooks/useCompareData';
import IndicatorTable from './components/IndicatorTable';
import MatchPanel from './components/MatchPanel';
import StatusBar from './components/StatusBar';

function App() {
  const {
    indicators, pdfNames, allResults,
    selectedId, currentMatches,
    progress,
    loading, analyzing, error,
    uploaded, analyzed,
    availableColors, selectedColors,
    selectedPdf, targetPage, highlightText, triggerKey,
    upload, analyze,
    selectIndicator, selectNextIndicator, selectPrevIndicator, setReviewStatus, handleManualBind,
    setSelectedPdf,
    doExport, doExportOriginal, doExportText, setError, toggleColor,
  } = useCompareData();

  const excelInputRef = useRef(null);
  const pdfInputRef = useRef(null);

  const [excelFile, setExcelFile] = useState(null);
  const [pdfFiles, setPdfFiles] = useState([]);

  const handleUpload = async () => {
    if (!excelFile || pdfFiles.length === 0) {
      setError('请选择 Excel 文件和至少一个 PDF 文件');
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
            disabled={loading || !excelFile || pdfFiles.length === 0}
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
            />
          ) : uploaded ? (
            <div className="flex-1 flex flex-col items-center justify-center p-6">
              <div className="text-center mb-6">
                <div className="text-4xl mb-3 opacity-80">🎨</div>
                <p className="text-slate-300 text-sm mb-1 font-medium">文件上传并解析成功</p>
                <p className="text-slate-500 text-xs">请选择要审查的单元格颜色（默认全选）</p>
              </div>
              <div className="flex flex-wrap gap-2 justify-center w-full">
                {availableColors.map(color => {
                  const isSelected = selectedColors.includes(color);
                  const isNoFill = color === '无填充';
                  return (
                    <button
                      key={color}
                      onClick={() => toggleColor(color)}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg border transition-all ${
                        isSelected 
                          ? 'border-blue-500/50 bg-blue-500/10 shadow-[0_0_10px_rgba(59,130,246,0.1)]' 
                          : 'border-white/10 bg-white/5 opacity-50 hover:opacity-80'
                      }`}
                    >
                      <div
                        className="w-3.5 h-3.5 rounded shadow-sm border border-white/20"
                        style={{ backgroundColor: isNoFill ? 'transparent' : color }}
                      />
                      <span className={`text-xs font-mono ${isSelected ? 'text-blue-400 font-medium' : 'text-slate-400'}`}>
                        {isNoFill ? '无填充' : color}
                      </span>
                    </button>
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
            onConfirm={(id) => setReviewStatus(id, '已确认')}
            onDispute={(id) => setReviewStatus(id, '存疑')}
            onNext={selectNextIndicator}
            onPrev={selectPrevIndicator}
            onManualBind={handleManualBind}
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
    </div>
  );
}

export default App;
