import React, { useState, useEffect } from 'react';

const DEFAULT_CONFIG = {
  bg_colors: { document: "#D6E4F0", yearbook: "#D9EDDF", url: "#FFF2CC" },
  font_colors: { unchecked: "#000000", confirmed: "#228B22", disputed: "#FF8C00" }
};

export default function ExportSettingsModal({ isOpen, onClose, onSave, initialConfig }) {
  const [config, setConfig] = useState(initialConfig || DEFAULT_CONFIG);
  const [warning, setWarning] = useState('');

  useEffect(() => {
    if (isOpen) {
      setConfig(initialConfig || DEFAULT_CONFIG);
    }
  }, [isOpen, initialConfig]);

  useEffect(() => {
    let newWarning = '';
    
    const hex2rgb = (hex) => {
        if (!hex) return [0,0,0];
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return [r, g, b];
    };
    
    const colorDist = (c1, c2) => {
        if (!c1 || !c2) return 1000;
        const [r1, g1, b1] = hex2rgb(c1);
        const [r2, g2, b2] = hex2rgb(c2);
        return Math.sqrt(Math.pow(r1-r2, 2) + Math.pow(g1-g2, 2) + Math.pow(b1-b2, 2));
    };

    const bgs = Object.entries(config.bg_colors);
    const fonts = Object.entries(config.font_colors);

    // 检查背景和字体
    for (const [, bgVal] of bgs) {
        for (const [, fontVal] of fonts) {
            if (colorDist(bgVal, fontVal) < 100) {
                newWarning = '警告：部分背景色与字体色可能难以区分';
            }
        }
    }
    
    // 检查背景色之间
    for (let i=0; i<bgs.length; i++) {
        for (let j=i+1; j<bgs.length; j++) {
            if (colorDist(bgs[i][1], bgs[j][1]) < 30) {
                newWarning = '警告：数据类型的背景色相互之间可能过于相近';
            }
        }
    }
    
    setWarning(newWarning);

  }, [config]);

  if (!isOpen) return null;

  const handleBgChange = (key, value) => {
    setConfig(prev => ({...prev, bg_colors: { ...prev.bg_colors, [key]: value }}));
  };

  const handleFontChange = (key, value) => {
    setConfig(prev => ({...prev, font_colors: { ...prev.font_colors, [key]: value }}));
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="bg-slate-800 border border-slate-700 rounded-xl shadow-2xl w-[400px] overflow-hidden flex flex-col">
        <div className="px-5 py-3 border-b border-white/10 flex items-center justify-between bg-slate-800/80">
          <h2 className="text-sm font-semibold text-slate-200">导出设置</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
        </div>
        
        <div className="p-5 space-y-6 flex-1 overflow-y-auto">
          {/* 数据类型颜色 */}
          <div className="space-y-3">
            <h3 className="text-xs font-medium text-slate-400 border-b border-white/5 pb-1">数据类型颜色（背景色）</h3>
            
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded" style={{ backgroundColor: config.bg_colors.document }}></div>
                <span className="text-xs text-slate-300" style={{ backgroundColor: config.bg_colors.document, color: '#000', padding: '2px 6px', borderRadius: '4px' }}>蓝色背景示例 (文档)</span>
              </div>
              <input type="color" value={config.bg_colors.document} onChange={e => handleBgChange('document', e.target.value)} className="w-6 h-6 p-0 border-0 rounded cursor-pointer bg-transparent" />
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded" style={{ backgroundColor: config.bg_colors.yearbook }}></div>
                <span className="text-xs text-slate-300" style={{ backgroundColor: config.bg_colors.yearbook, color: '#000', padding: '2px 6px', borderRadius: '4px' }}>绿色背景示例 (年鉴)</span>
              </div>
              <input type="color" value={config.bg_colors.yearbook} onChange={e => handleBgChange('yearbook', e.target.value)} className="w-6 h-6 p-0 border-0 rounded cursor-pointer bg-transparent" />
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded" style={{ backgroundColor: config.bg_colors.url }}></div>
                <span className="text-xs text-slate-300" style={{ backgroundColor: config.bg_colors.url, color: '#000', padding: '2px 6px', borderRadius: '4px' }}>黄色背景示例 (URL)</span>
              </div>
              <input type="color" value={config.bg_colors.url} onChange={e => handleBgChange('url', e.target.value)} className="w-6 h-6 p-0 border-0 rounded cursor-pointer bg-transparent" />
            </div>
          </div>

          {/* 核对状态颜色 */}
          <div className="space-y-3">
            <h3 className="text-xs font-medium text-slate-400 border-b border-white/5 pb-1">核对状态颜色（字体色）</h3>
            
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: config.font_colors.unchecked }}>未核对</span>
              </div>
              <input type="color" value={config.font_colors.unchecked} onChange={e => handleFontChange('unchecked', e.target.value)} className="w-6 h-6 p-0 border-0 rounded cursor-pointer bg-transparent" />
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: config.font_colors.confirmed }}>已核对</span>
              </div>
              <input type="color" value={config.font_colors.confirmed} onChange={e => handleFontChange('confirmed', e.target.value)} className="w-6 h-6 p-0 border-0 rounded cursor-pointer bg-transparent" />
            </div>

            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: config.font_colors.disputed }}>未核对</span>
              </div>
              <input type="color" value={config.font_colors.disputed} onChange={e => handleFontChange('disputed', e.target.value)} className="w-6 h-6 p-0 border-0 rounded cursor-pointer bg-transparent" />
            </div>
          </div>

          {warning && (
            <div className="text-xs text-amber-400 bg-amber-400/10 p-2 rounded">
              {warning}
            </div>
          )}

        </div>

        <div className="px-5 py-3 border-t border-white/10 flex items-center justify-between bg-slate-800/80">
          <button onClick={() => setConfig(DEFAULT_CONFIG)} className="text-xs text-slate-400 hover:text-slate-300">恢复默认</button>
          <div className="flex gap-2">
            <button onClick={onClose} className="px-4 py-1.5 rounded bg-white/5 hover:bg-white/10 text-slate-300 text-xs transition-colors">取消</button>
            <button onClick={() => onSave(config)} className="px-4 py-1.5 rounded bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 border border-blue-500/30 text-xs transition-colors">确定</button>
          </div>
        </div>
      </div>
    </div>
  );
}
