import { useState } from 'react';

export default function PdfStatusBar({ indicator, matchInfo, onConfirm, onDispute, onPrev, onNext }) {
  if (!indicator) return null;

  let statusLabel = '未核对';
  let statusStyle = 'bg-slate-800 text-slate-400 border-slate-700';

  if (indicator.review_status === '已核对') {
    statusLabel = '🟢 已核对';
    statusStyle = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
  } else if (indicator.review_status === '未核对') {
    statusLabel = '🟡 未核对';
    statusStyle = 'bg-amber-500/10 text-amber-400 border-amber-500/20';
  }

  return (
    <div className="flex items-center justify-between px-5 py-3 border-b border-white/5 bg-slate-900/80 shrink-0">
      <div className="flex items-center gap-4">
        {/* 指标名称与来源标签（替换原来的年份标签） */}
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-white">{indicator.name}</h2>
          {(() => {
            // 与左侧 IndicatorTable 一致的来源标签逻辑
            const getSourceLabel = (ind) => {
              if (ind.source_file_yearbook) return '年鉴';
              if (ind.source_file_report) return '司局';
              if (ind.source_file_url) return 'AI';
              return null;
            };
            const sourceLabel = getSourceLabel(indicator);
            const yearShort = indicator.year ? indicator.year.replace('年', '') : '';
            const displayText = sourceLabel ? `${yearShort}${sourceLabel}` : (indicator.year || '');

            let badgeStyle = 'bg-purple-500/15 text-purple-400 border-purple-500/20';
            if (sourceLabel === '年鉴') {
              badgeStyle = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/20';
            } else if (sourceLabel === '司局') {
              badgeStyle = 'bg-blue-500/15 text-blue-400 border-blue-500/20';
            } else if (sourceLabel === 'AI') {
              badgeStyle = 'bg-amber-500/15 text-amber-400 border-amber-500/20';
            }

            return (
              <span className={`text-xs px-2 py-0.5 rounded-full ${badgeStyle}`}>
                {displayText}
              </span>
            );
          })()}
        </div>

        <div className="w-px h-4 bg-white/10"></div>

        {/* 目标值 */}
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">Excel目标值:</span>
          <span className="font-mono text-sm font-semibold text-blue-400">
            {indicator.target_value.toLocaleString()}
            {indicator.unit && <span className="ml-1 font-normal text-xs">{indicator.unit}</span>}
          </span>
        </div>

        <div className="w-px h-4 bg-white/10"></div>

        {/* 状态与匹配信息 */}
        <div className="flex items-center gap-2">
          <span className={`px-2 py-1 text-xs rounded border ${statusStyle}`}>
            {statusLabel}
          </span>
          {(() => {
            // 匹配状态信息
            const matchedPage = indicator.matched_page;
            const sourceFile = indicator.source_file || '';
            const pageMatch = sourceFile.match(/\+P(\d+)/i);
            const parsedPage = pageMatch ? parseInt(pageMatch[1], 10) : null;

            let matchStatusLabel = '';
            let matchStatusStyle = '';

            if (matchInfo && matchInfo.matched_value_raw) {
              matchStatusLabel = '🟢 已定位';
              matchStatusStyle = 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
            } else if (matchedPage || parsedPage) {
              matchStatusLabel = `🟡 第${matchedPage || parsedPage}页，数值未找到`;
              matchStatusStyle = 'bg-amber-500/10 text-amber-400 border-amber-500/20';
            } else {
              matchStatusLabel = '🔴 未找到页码信息';
              matchStatusStyle = 'bg-red-500/10 text-red-400 border-red-500/20';
            }

            return (
              <span className={`px-2 py-1 text-xs rounded border ${matchStatusStyle}`} title={`AI检索数据来源解析结果`}>
                {matchStatusLabel}
              </span>
            );
          })()}
          {matchInfo && (
            <span className="text-xs text-slate-400">
              {matchInfo.is_match ? '一致' : '差异'} ({matchInfo.confidence}%)
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
                <button
          onClick={onPrev}
          className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300 transition-colors group relative"
          title="上一项 (↑)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 15l7-7 7 7" /></svg>
          <span className="absolute -top-1 -right-1 text-[9px] bg-slate-700 text-slate-300 px-1 rounded leading-none opacity-0 group-hover:opacity-100 transition-opacity">↑</span>
        </button>
        <button
          onClick={onNext}
          className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300 transition-colors group relative"
          title="下一项 (↓)"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7 7" /></svg>
          <span className="absolute -top-1 -right-1 text-[9px] bg-slate-700 text-slate-300 px-1 rounded leading-none opacity-0 group-hover:opacity-100 transition-opacity">↓</span>
        </button>

        <div className="w-px h-4 bg-white/10 mx-1"></div>

                <button
          onClick={() => onConfirm?.(indicator.id)}
          className="px-3 py-1.5 text-xs rounded-lg bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-400 border border-emerald-500/30 transition-all flex items-center gap-1 group relative"
          title="已核对 (Enter)"
        >
          ✓ 已核对
          <kbd className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/20 border border-emerald-500/30 ml-1 opacity-0 group-hover:opacity-100 transition-opacity">↵</kbd>
        </button>
                <button
          onClick={() => onDispute?.(indicator.id)}
          className="px-3 py-1.5 text-xs rounded-lg bg-amber-500/15 hover:bg-amber-500/25 text-amber-400 border border-amber-500/30 transition-all flex items-center gap-1 group relative"
          title="未核对 (Space)"
        >
          ✗ 未核对
          <kbd className="text-[9px] px-1 py-0.5 rounded bg-amber-500/20 border border-amber-500/30 ml-1 opacity-0 group-hover:opacity-100 transition-opacity">␣</kbd>
        </button>
      </div>
    </div>
  );
}
