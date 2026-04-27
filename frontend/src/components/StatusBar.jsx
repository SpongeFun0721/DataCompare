/**
 * StatusBar - 底部状态栏
 *
 * 显示核对进度、按状态统计、导出按钮。
 */

export default function StatusBar({ progress, analyzed, onExport, onExportOriginal }) {
  if (!analyzed) {
    return (
      <div className="px-4 py-2.5 border-t border-white/5 bg-slate-900/50 backdrop-blur-sm">
        <p className="text-xs text-slate-500">等待分析...</p>
      </div>
    );
  }

  const { total, confirmed, unchecked } = progress;
  const percent = total > 0 ? Math.round((confirmed / total) * 100) : 0;

  return (
    <div className="px-4 py-2.5 border-t border-white/5 bg-slate-900/50 backdrop-blur-sm">
      <div className="flex items-center justify-between">
        {/* 进度条 */}
        <div className="flex items-center gap-3 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-slate-400">核对进度</span>
            <span className="text-xs font-mono font-semibold text-white">
              {confirmed}/{total}
            </span>
          </div>

          <div className="w-32 h-1.5 rounded-full bg-slate-700 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-blue-500 to-purple-500 transition-all duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>

          <span className="text-xs font-mono text-slate-400">{percent}%</span>
        </div>

        {/* 状态统计 */}
        <div className="flex items-center gap-3 mx-4 text-xs">
          <span className="flex items-center gap-1 text-emerald-400">
            🟢 已核对 <b>{confirmed}</b>
          </span>
          <span className="flex items-center gap-1 text-amber-400">
            🟡 未核对 <b>{unchecked}</b>
          </span>
        </div>

        {/* 操作按钮区 */}
        <div className="flex gap-2">
          <button
            onClick={onExport}
            disabled={!analyzed}
            className="px-4 py-1.5 rounded-lg text-sm bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border border-amber-500/20 transition-all shadow-sm shadow-amber-500/10 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-2"
          >
            导出报告表
          </button>
          <button
            onClick={onExportOriginal}
            disabled={!analyzed}
            className="px-4 py-1.5 rounded-lg text-sm bg-gradient-to-r from-emerald-600 to-teal-500 hover:from-emerald-500 hover:to-teal-400 text-white shadow-lg shadow-emerald-500/20 transition-all disabled:opacity-40 disabled:cursor-not-allowed font-medium flex items-center gap-2 group relative"
            title="导出标色原表 (Ctrl+E)"

          >
            导出标色原表
        <kbd className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/30 border border-emerald-400/40 ml-1 opacity-0 group-hover:opacity-100 transition-opacity">Ctrl+E</kbd>
          </button>

        </div>
      </div>
    </div>
  );
}
