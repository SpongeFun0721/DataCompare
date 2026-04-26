/**
 * useKeyboardShortcuts Hook
 *
 * 全局快捷键系统：
 * - ↑↓ 切换指标
 * - Enter 确认、Space 争议、Delete 未找到
 * - PgUp/PgDn PDF 翻页
 * - 1-4 切换年份筛选
 * - ? 弹出快捷键面板
 * - Ctrl+Z 撤销
 * - Ctrl+E 导出
 * - 操作后自动跳下一个未核对指标
 */

import { useEffect, useCallback, useState, useRef } from 'react';

const SHORTCUTS = [
  { key: 'ArrowUp',   desc: '↑',   action: '切换到上一个指标' },
  { key: 'ArrowDown', desc: '↓',   action: '切换到下一个指标' },
  { key: 'Enter',     desc: '↵',   action: '确认当前指标' },
  { key: ' ',         desc: '␣',   action: '标记为未核对' },
  { key: 'PageUp',    desc: 'PgUp', action: 'PDF 上一页' },
  { key: 'PageDown',  desc: 'PgDn', action: 'PDF 下一页' },
  { key: '1-4',       desc: '1-4',  action: '切换年份筛选' },
  { key: '?',         desc: '?',    action: '显示/隐藏快捷键面板' },
  { key: 'Ctrl+Z',    desc: 'Ctrl+Z', action: '撤销上一步操作' },
  { key: 'Ctrl+E',    desc: 'Ctrl+E', action: '导出报告' },
];

export function useKeyboardShortcuts({
  // 指标导航
  selectedId,
  allResults,
  selectIndicator,
  selectNextIndicator,
  selectPrevIndicator,
  // 状态操作
  setReviewStatus,
  // PDF 翻页
  currentPage,
  numPages,
  onPageChange,
  // 年份筛选（由 IndicatorTable 控制）
  onYearFilter,
  currentYearFilter,
  // 导出
  onExport,
  // 撤销（由 useCompareData 提供）
  onUndo,
}) {
  const [showShortcutsPanel, setShowShortcutsPanel] = useState(false);
  const [toast, setToast] = useState(null); // { message, type }
  const toastTimer = useRef(null);

  // 使用 ref 缓存最新值，避免事件监听器闭包过期
  const selectedIdRef = useRef(selectedId);
  const allResultsRef = useRef(allResults);

  // 同步 ref
  useEffect(() => { selectedIdRef.current = selectedId; }, [selectedId]);
  useEffect(() => { allResultsRef.current = allResults; }, [allResults]);

  // 显示短暂提示
  const showToast = useCallback((message, type = 'info') => {
    setToast({ message, type });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 1500);
  }, []);

  // 查找下一个未核对的指标（基于当前的 allResults 状态）
  const findNextUnchecked = useCallback((fromIndex) => {
    const currentAllResults = allResultsRef.current;
    if (!currentAllResults?.length) return -1;
    const start = fromIndex + 1;
    for (let i = start; i < currentAllResults.length; i++) {
      if (currentAllResults[i].indicator.review_status === '未核对') {
        return i;
      }
    }
    // 如果后面没有未核对的，从头开始找
    for (let i = 0; i < fromIndex; i++) {
      if (currentAllResults[i].indicator.review_status === '未核对') {
        return i;
      }
    }
    return -1; // 全部已核对
  }, []);

  // 核后回调：设置状态并立即跳转到下一个未核对指标
  // 使用 ref 避免闭包过期问题
  const confirmAndAdvance = useCallback((status) => {
    const currentId = selectedIdRef.current;
    const currentAllResults = allResultsRef.current;

    if (currentId === null) {
      showToast('请先选择一个指标', 'warning');
      return;
    }

    // 调用原始 setReviewStatus（会触发异步 API 和本地更新）
    setReviewStatus(currentId, status);

    // 获取当前索引
    const currentIdx = currentAllResults.findIndex(r => r.indicator.id === currentId);
    if (currentIdx < 0) {
      selectNextIndicator();
      return;
    }

    // 计算下一个未核对的索引（跳过当前索引）
    let nextIdx = -1;
    // 先从 currentIdx + 1 往后找
    for (let i = currentIdx + 1; i < currentAllResults.length; i++) {
      if (currentAllResults[i].indicator.review_status === '未核对') {
        nextIdx = i;
        break;
      }
    }
    // 如果后面没有，从头找到 currentIdx
    if (nextIdx < 0) {
      for (let i = 0; i < currentIdx; i++) {
        if (currentAllResults[i].indicator.review_status === '未核对') {
          nextIdx = i;
          break;
        }
      }
    }

    if (nextIdx >= 0 && nextIdx < currentAllResults.length && currentAllResults[nextIdx]) {
      selectIndicator(currentAllResults[nextIdx].indicator.id);
    } else {
      // 全部已核对或没找到未核对的，跳到下一项
      if (currentIdx < currentAllResults.length - 1) {
        selectIndicator(currentAllResults[currentIdx + 1].indicator.id);
      }
    }
  }, [setReviewStatus, showToast, selectIndicator, selectNextIndicator]);

  // 撤销栈
  const [undoStack, setUndoStack] = useState([]);
  const MAX_UNDO = 50;

  // 记录操作用于撤销
  const pushUndo = useCallback((action) => {
    setUndoStack(prev => {
      const newStack = [...prev, action];
      if (newStack.length > MAX_UNDO) newStack.shift();
      return newStack;
    });
  }, []);

  // 撤销上一次操作
  const handleUndo = useCallback(() => {
    setUndoStack(prev => {
      if (prev.length === 0) {
        showToast('没有可撤销的操作', 'warning');
        return prev;
      }
      const lastAction = prev[prev.length - 1];
      if (lastAction.type === 'review_status' && onUndo) {
        onUndo(lastAction);
        showToast('已撤销: ' + lastAction.status, 'info');
      }
      return prev.slice(0, -1);
    });
  }, [onUndo, showToast]);

  // 年份筛选循环
  const yearFilters = ['全部', '2021', '2022', '2023', '2024'];
  const handleYearFilter = useCallback((key) => {
    if (!onYearFilter) return;
    const num = parseInt(key);
    if (num >= 1 && num <= 4) {
      const year = yearFilters[num];
      if (year !== currentYearFilter) {
        onYearFilter(year);
        showToast(`筛选: ${year}年`, 'info');
      }
    }
  }, [onYearFilter, currentYearFilter, showToast]);

  // 主键盘事件处理
  useEffect(() => {
    const handleKeyDown = (e) => {
      // 如果焦点在输入框/文本框中，不处理全局快捷键
      const tag = document.activeElement?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
        // 但 ? 和 Escape 仍然有效
        if (e.key !== '?' && e.key !== 'Escape') return;
      }

      // Ctrl 组合键
      if (e.ctrlKey || e.metaKey) {
        switch (e.key.toLowerCase()) {
          case 'z':
            e.preventDefault();
            handleUndo();
            return;
          case 'e':
            e.preventDefault();
            if (onExport) onExport();
            showToast('正在导出报告...', 'info');
            return;
        }
        return;
      }

      switch (e.key) {
        // ↑ 上一个指标
        case 'ArrowUp':
          e.preventDefault();
          selectPrevIndicator();
          showToast('上一个指标', 'info');
          break;

        // ↓ 下一个指标
        case 'ArrowDown':
          e.preventDefault();
          selectNextIndicator();
          showToast('下一个指标', 'info');
          break;

        // Enter 确认
        case 'Enter':
          e.preventDefault();
          confirmAndAdvance('已核对');
          showToast('已核对 ✓ 自动跳转', 'success');
          break;

        // Space 争议
        case ' ':
          e.preventDefault();
          confirmAndAdvance('未核对');
          showToast('标记为未核对 ⚠', 'warning');
          break;

        // PgUp PDF 上一页
        case 'PageUp':
          e.preventDefault();
          if (onPageChange && currentPage > 1) {
            onPageChange(currentPage - 1);
          }
          break;

        // PgDn PDF 下一页
        case 'PageDown':
          e.preventDefault();
          if (onPageChange && numPages && currentPage < numPages) {
            onPageChange(currentPage + 1);
          }
          break;

        // 1-4 年份筛选
        case '1':
        case '2':
        case '3':
        case '4':
          e.preventDefault();
          handleYearFilter(e.key);
          break;

        // ? 快捷键面板开关
        case '?':
        case '/':
          if (e.key === '/' && e.shiftKey) break; // 实际是 ?
          e.preventDefault();
          setShowShortcutsPanel(prev => !prev);
          break;

        // Escape 关闭面板
        case 'Escape':
          if (showShortcutsPanel) {
            setShowShortcutsPanel(false);
          }
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    selectedId, allResults,
    selectPrevIndicator, selectNextIndicator, confirmAndAdvance,
    onPageChange, currentPage, numPages,
    handleYearFilter, currentYearFilter,
    onExport, handleUndo, showShortcutsPanel,
    showToast, pushUndo,
  ]);

  // 组件卸载时清理 toast 计时器
  useEffect(() => {
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, []);

  return {
    showShortcutsPanel,
    setShowShortcutsPanel,
    toast,
    SHORTCUTS,
    pushUndo,
  };
}
