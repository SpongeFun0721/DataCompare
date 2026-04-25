import React from 'react';
import PdfViewer from './PdfViewer';
import PdfStatusBar from './PdfStatusBar';
import { getPdfUrl } from '../api';

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
  onNext,
  onPrev,
  onManualBind
}) {
  const indicator = currentMatches?.indicator;

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

    // 打印匹配到的实际文本
    if (currentPdfMatchInfo) {
      console.log('  - 匹配值:', currentPdfMatchInfo.matched_value_raw);
      console.log('  - 上下文:', currentPdfMatchInfo.context);
    }
  }, [highlightText, targetPage, indicator, currentPdfMatchInfo]);

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

  const pdfUrl = selectedPdf ? getPdfUrl(selectedPdf) : null;

  return (
    <div className="flex flex-col h-full relative">
      {/* 顶部状态条：显示匹配指标信息和操作按钮 */}
      <PdfStatusBar
        indicator={indicator}
        matchInfo={currentPdfMatchInfo}
        onConfirm={onConfirm}
        onDispute={onDispute}
        onPrev={onPrev}
        onNext={onNext}
      />

      {/* PDF 选择器（如果有多个 PDF） */}
      {pdfNames && pdfNames.length > 1 && (
        <div className="px-4 py-2 bg-slate-800/50 border-b border-white/5 flex items-center gap-2">
          <span className="text-xs text-slate-500">查看报告:</span>
          <select
            value={selectedPdf || ''}
            onChange={(e) => onPdfChange(e.target.value)}
            className="bg-slate-900 border border-white/10 text-slate-300 text-xs rounded px-2 py-1 outline-none focus:border-blue-500/50"
          >
            {pdfNames.map(name => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
        </div>
      )}

      {/* PDF 阅读器区域 */}
      <div className="flex-1 min-h-0">
        <PdfViewer
          pdfUrl={pdfUrl}
          targetPage={targetPage}
          highlightText={highlightText}
          triggerKey={triggerKey}
          onTextSelect={onManualBind}
        />
      </div>
    </div>
  );
}
