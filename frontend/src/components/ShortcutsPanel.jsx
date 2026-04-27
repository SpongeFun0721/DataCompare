/**
 * ShortcutsPanel - 快捷键说明面板
 *
 * 悬浮在页面中央的模态框，显示所有快捷键列表。
 * 点击 ? 或面板上的关闭按钮可触发显示/隐藏。
 */

import React from 'react';

const SHORTCUTS_GROUPS = [
  {
    title: '指标导航',
    items: [
      { keys: ['↑'], desc: '上一个指标' },
      { keys: ['↓'], desc: '下一个指标' },
    ],
  },
  {
    title: '核对操作',
    items: [
      { keys: ['↵'], desc: '确认当前指标' },
      { keys: ['␣'], desc: '标记为未核对' },
    ],
  },
  {
    title: 'PDF 阅读',
    items: [
      { keys: ['PgUp'], desc: 'PDF 上一页' },
      { keys: ['PgDn'], desc: 'PDF 下一页' },
    ],
  },
  {
    title: '筛选与视图',
    items: [
      { keys: ['1'], desc: '筛选全部年份' },
      { keys: ['2'], desc: '筛选 2021 年' },
      { keys: ['3'], desc: '筛选 2022 年' },
      { keys: ['4'], desc: '筛选 2023 年' },
    ],
  },
  {
    title: '系统操作',
    items: [
      { keys: ['Ctrl', 'Z'], desc: '撤销上一步操作' },
      { keys: ['Ctrl', 'E'], desc: '导出标色原表' },
      { keys: ['?'], desc: '显示/隐藏本面板' },
      { keys: ['Esc'], desc: '关闭本面板' },
    ],
  },
];

export default function ShortcutsPanel({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="bg-slate-800 border border-slate-700 rounded-xl shadow-2xl w-[480px] max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 标题 */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-white/10">
          <div className="flex items-center gap-2">
            <span className="text-lg">⌨️</span>
            <h2 className="text-sm font-semibold text-slate-200">快捷键指南</h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 快捷键列表 */}
        <div className="p-5 space-y-5">
          {SHORTCUTS_GROUPS.map((group) => (
            <div key={group.title}>
              <h3 className="text-xs font-medium text-slate-400 mb-2 uppercase tracking-wider">
                {group.title}
              </h3>
              <div className="space-y-1.5">
                {group.items.map((item) => (
                  <div
                    key={item.keys.join('+')}
                    className="flex items-center justify-between py-1.5 px-2 rounded-lg hover:bg-white/5 transition-colors"
                  >
                    <span className="text-sm text-slate-300">{item.desc}</span>
                    <div className="flex items-center gap-1">
                      {item.keys.map((key, idx) => (
                        <React.Fragment key={idx}>
                          {idx > 0 && (
                            <span className="text-xs text-slate-500">+</span>
                          )}
                          <kbd className="px-2 py-0.5 text-xs font-mono bg-slate-900 border border-slate-600 rounded text-slate-200 shadow-sm">
                            {key}
                          </kbd>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* 底部提示 */}
        <div className="px-5 py-3 border-t border-white/10 bg-slate-900/50">
          <p className="text-xs text-slate-500">
            💡 提示：操作（确认/未核对）后将自动跳转到下一个未核对的指标
          </p>
        </div>
      </div>
    </div>
  );
}
