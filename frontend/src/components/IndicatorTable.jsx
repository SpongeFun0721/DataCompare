/**
 * IndicatorTable - 左侧指标表格
 *
 * 功能：
 * - 显示所有 Excel 指标（序号、名称、目标值、单位、核对状态）
 * - 点击行高亮并触发右侧刷新
 * - 支持搜索过滤
 * - 固定表头，内容区滚动
 */

import { useState, useMemo, useEffect, useRef } from 'react';

const STATUS_ICONS = {
  '未核对': { icon: '🔵', bg: 'bg-blue-500/10', text: 'text-blue-400' },
  '已确认': { icon: '🟢', bg: 'bg-emerald-500/10', text: 'text-emerald-400' },
  '存疑':   { icon: '🟡', bg: 'bg-amber-500/10', text: 'text-amber-400' },
};

export default function IndicatorTable({ indicators, selectedId, onSelect, allResults, pdfNames, yearFilter = '全部' }) {
  const [search, setSearch] = useState('');

    const filtered = useMemo(() => {
    let list = indicators;
    // 年份筛选
    if (yearFilter && yearFilter !== '全部') {
      list = list.filter(ind => ind.year === yearFilter);
    }
    // 搜索筛选
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      list = list.filter(ind =>
        ind.name.toLowerCase().includes(q) ||
        String(ind.target_value).includes(q) ||
        (ind.year && ind.year.includes(q))
      );
    }
    return list;
  }, [indicators, search, yearFilter]);

  // 获取指标的匹配概况
  const getMatchSummary = (indicatorId) => {
    const result = allResults.find(r => r.indicator.id === indicatorId);
    if (!result) return null;
    const total = pdfNames.length;
    const matched = Object.values(result.best_matches || {}).filter(m => m && m.is_match).length;
    return { total, matched };
  };

  const selectedRowRef = useRef(null);

  useEffect(() => {
    if (selectedRowRef.current) {
      selectedRowRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [selectedId]);

  return (
    <div className="flex flex-col h-full">
            {/* 搜索框 + 年份筛选 */}
      <div className="p-3 border-b border-white/5 space-y-2">
        <div className="relative">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            placeholder="搜索指标..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/25 transition-all"
          />
        </div>
        {/* 年份筛选提示 */}
        {yearFilter && yearFilter !== '全部' && (
          <div className="flex items-center gap-1.5 px-1">
            <span className="text-[10px] text-slate-500">筛选:</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400 border border-purple-500/20">
              {yearFilter}年
            </span>
            <span className="text-[10px] text-slate-500 ml-auto">
              快捷键: <kbd className="px-1 py-0.5 bg-slate-700 rounded text-slate-300 text-[9px]">1</kbd> 全部
            </span>
          </div>
        )}
      </div>

      {/* 表格 */}
      <div className="flex-1 overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10">
            <tr className="bg-slate-800/90 backdrop-blur-sm">
              <th className="text-left px-3 py-2.5 text-xs font-semibold text-slate-400 uppercase tracking-wider w-8">#</th>
              <th className="text-left px-3 py-2.5 text-xs font-semibold text-slate-400 uppercase tracking-wider">指标</th>
              <th className="text-right px-3 py-2.5 text-xs font-semibold text-slate-400 uppercase tracking-wider w-24">目标值</th>
              <th className="text-center px-3 py-2.5 text-xs font-semibold text-slate-400 uppercase tracking-wider w-14">状态</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((ind, idx) => {
              const isSelected = ind.id === selectedId;
              const status = STATUS_ICONS[ind.review_status] || STATUS_ICONS['未核对'];
              const summary = getMatchSummary(ind.id);

              return (
                <tr
                  key={ind.id}
                  ref={isSelected ? selectedRowRef : null}
                  onClick={() => onSelect(ind.id)}
                  className={`
                    cursor-pointer border-b border-white/5 transition-all duration-150
                    ${isSelected
                      ? 'bg-blue-500/15 border-l-2 border-l-blue-500'
                      : 'hover:bg-white/5 border-l-2 border-l-transparent'
                    }
                  `}
                >
                  <td className="px-3 py-3 text-slate-500 text-xs">{idx + 1}</td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium text-slate-200 leading-tight">{ind.name}</span>
                      {ind.year && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400 border border-purple-500/20 whitespace-nowrap">
                          {ind.year}
                        </span>
                      )}
                    </div>
                    {ind.unit && (
                      <span className="text-xs text-slate-500">({ind.unit})</span>
                    )}
                    {summary && (
                      <div className="mt-0.5">
                        <span className="text-xs text-slate-500">
                          匹配 {summary.matched}/{summary.total}
                        </span>
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-right font-mono text-sm text-slate-300">
                    {ind.target_value.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-center">
                    <span className={`inline-flex items-center justify-center w-7 h-7 rounded-full text-sm ${status.bg}`}>
                      {status.icon}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filtered.length === 0 && (
          <div className="text-center py-12 text-slate-500 text-sm">
            {indicators.length === 0 ? '暂无指标数据' : '无匹配结果'}
          </div>
        )}
      </div>
    </div>
  );
}
