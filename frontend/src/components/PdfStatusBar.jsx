import { useState, useCallback, useEffect, useRef } from 'react';

export default function PdfStatusBar({ indicator, matchInfo, onConfirm, onDispute, onPrev, onNext, onSaveComment }) {
  const [commentText, setCommentText] = useState('');
  const [showCommentInput, setShowCommentInput] = useState(false);
  const [saving, setSaving] = useState(false);
  const prevIndicatorIdRef = useRef(indicator?.id);
  const showCommentInputRef = useRef(false);
  const commentTextRef = useRef('');
  const onSaveCommentRef = useRef(onSaveComment);

  // 同步 ref 与 state，确保 useEffect 能拿到最新值
  showCommentInputRef.current = showCommentInput;
  commentTextRef.current = commentText;
  onSaveCommentRef.current = onSaveComment;

  // ---- 自动保存批注 & 重置状态 ----
  // 当 indicator 变化时（用户切换到另一个数据项）：
  // 1. 如果批注输入框打开且有未保存内容，自动保存到上一个指标
  // 2. 重置批注状态（清空输入框、关闭面板）
  useEffect(() => {
    const prevId = prevIndicatorIdRef.current;
    prevIndicatorIdRef.current = indicator?.id;

    // indicator 刚加载或未变化时跳过
    if (!prevId || !indicator || prevId === indicator.id) return;

    // 切换指标时，如果输入框打开且有内容，自动保存到上一个指标
    // 使用 ref 获取最新值，避免因闭包捕获旧值导致无法保存
    if (showCommentInputRef.current && commentTextRef.current.trim()) {
      onSaveCommentRef.current?.(prevId, commentTextRef.current.trim());
    }

    // 重置批注状态
    setShowCommentInput(false);
    setCommentText('');
  }, [indicator?.id]);

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

  const handleSaveComment = useCallback(async () => {
    if (onSaveComment && commentText.trim()) {
      setSaving(true);
      try {
        await onSaveComment(indicator.id, commentText.trim());
        setShowCommentInput(false);
      } finally {
        setSaving(false);
      }
    }
  }, [onSaveComment, commentText, indicator.id]);

  const handleDeleteComment = useCallback(async () => {
    if (onSaveComment) {
      setSaving(true);
      try {
        await onSaveComment(indicator.id, '');
      } finally {
        setSaving(false);
      }
      setShowCommentInput(false);
      setCommentText('');
    }
  }, [onSaveComment, indicator.id]);

  const handleCancelComment = useCallback(() => {
    setShowCommentInput(false);
    setCommentText('');
  }, []);

  const handleOpenComment = useCallback(() => {
    setCommentText(indicator.note || '');
    setShowCommentInput(true);
  }, [indicator.note]);

  return (
    <div className="flex flex-col shrink-0">
      {/* 主状态栏 */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-white/5 bg-slate-900/80">
        <div className="flex items-center gap-4">
          {/* 指标名称与来源标签 */}
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-white">{indicator.name}</h2>
            {(() => {
              const getSourceLabel = (ind) => {
                if (ind.source_file_yearbook) return '年鉴';
                if (ind.source_file_report) return '司局';
                if (ind.source_file_url) return 'AI';
                return null;
              };
              const sourceLabel = getSourceLabel(indicator);
              const yearShort = indicator.year ? indicator.year.replace('年', '') : '';
              const pageSuffix = indicator.matched_page ? `P${indicator.matched_page}` : '';
              const displayText = sourceLabel ? `${yearShort}${sourceLabel}${pageSuffix}` : (indicator.year || '');

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
              {indicator.extracted_display || indicator.target_value.toLocaleString()}
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
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" /></svg>
            <span className="absolute -top-1 -right-1 text-[9px] bg-slate-700 text-slate-300 px-1 rounded leading-none opacity-0 group-hover:opacity-100 transition-opacity">↓</span>
          </button>

          <div className="w-px h-4 bg-white/10 mx-1"></div>

          {/* 批注按钮 */}
          <button
            onClick={handleOpenComment}
            className={`px-2 py-1.5 text-xs rounded-lg border transition-all flex items-center gap-1 group relative ${
              indicator.note
                ? 'bg-blue-500/15 text-blue-400 border-blue-500/30 hover:bg-blue-500/25'
                : 'bg-white/5 text-slate-400 border-white/10 hover:bg-white/10 hover:text-slate-300'
            }`}
            title={indicator.note ? `查看/编辑批注: ${indicator.note}` : '添加批注'}
          >
            💬 {indicator.note ? '批注' : '批注'}
            {indicator.note && (
              <span className="absolute -top-1 -right-1 w-2 h-2 bg-blue-400 rounded-full" />
            )}
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

      {/* 批注输入区域 */}
      {showCommentInput && (
        <div className="px-5 py-2 bg-slate-800/60 border-b border-white/5 flex items-start gap-3">
          <div className="flex-1">
            <textarea
              value={commentText}
              onChange={(e) => setCommentText(e.target.value)}
              placeholder="输入批注内容（将写入导出标色原表的单元格批注中）..."
              className="w-full bg-slate-900 border border-white/10 text-slate-200 text-xs rounded px-3 py-2 outline-none focus:border-blue-500/50 resize-none"
              rows={2}
              autoFocus
            />
          </div>
          <div className="flex items-center gap-2 pt-1">
            <button
              onClick={handleSaveComment}
              disabled={!commentText.trim() || saving}
              className="px-3 py-1.5 text-xs rounded-lg bg-blue-500/15 hover:bg-blue-500/25 text-blue-400 border border-blue-500/30 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {saving ? '保存中...' : '保存'}
            </button>
            {/* 删除批注按钮：已有批注时才显示 */}
            {indicator.note && (
              <button
                onClick={handleDeleteComment}
                disabled={saving}
                className="px-3 py-1.5 text-xs rounded-lg bg-red-500/15 hover:bg-red-500/25 text-red-400 border border-red-500/30 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {saving ? '删除中...' : '删除'}
              </button>
            )}
            <button
              onClick={handleCancelComment}
              className="px-3 py-1.5 text-xs rounded-lg bg-white/5 hover:bg-white/10 text-slate-400 border border-white/10 transition-all"
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}