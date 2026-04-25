/**
 * Toast - 短暂的提示通知
 *
 * 显示在页面右下角或顶部，用于快捷键操作的反馈。
 * 支持 success / warning / error / info 四种类型。
 */

export default function Toast({ toast }) {
  if (!toast) return null;

  const typeStyles = {
    success: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    warning: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    error: 'bg-red-500/20 text-red-400 border-red-500/30',
    info: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  };

  const typeIcons = {
    success: '✓',
    warning: '⚠',
    error: '✗',
    info: 'ℹ',
  };

  const style = typeStyles[toast.type] || typeStyles.info;
  const icon = typeIcons[toast.type] || typeIcons.info;

  return (
    <div className="fixed bottom-20 left-1/2 -translate-x-1/2 z-50 animate-fade-in-up pointer-events-none">
      <div className={`px-4 py-2 rounded-lg border backdrop-blur-sm shadow-lg ${style} flex items-center gap-2 text-sm`}>
        <span className="font-mono text-xs">{icon}</span>
        <span>{toast.message}</span>
      </div>
    </div>
  );
}
