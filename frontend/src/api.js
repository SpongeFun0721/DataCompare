/**
 * API 调用封装模块
 *
 * 所有与后端的 HTTP 通信均通过此模块完成，
 * 前端组件只需调用这些函数即可。
 */

const BASE = '/api';

/**
 * 通用请求函数
 */
async function request(url, options = {}) {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `请求失败: ${res.status}`);
  }

  // 如果是文件下载，返回 blob
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('spreadsheet') || contentType.includes('octet-stream')) {
    return res.blob();
  }

  return res.json();
}

/**
 * 上传 Excel + PDF 文件
 * @param {File} excelFile - Excel 文件
 * @param {File[]} pdfFiles - PDF 文件数组
 */
export async function uploadFiles(excelFile, pdfFiles) {
  const formData = new FormData();
  formData.append('excel', excelFile);
  for (const pdf of pdfFiles) {
    formData.append('pdfs', pdf);
  }

  const res = await fetch(`${BASE}/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || '上传失败');
  }

  return res.json();
}

/**
 * 触发分析
 * @param {string[]} selectedColors - 要分析的颜色列表
 * @param {Object} colorMapping - 颜色到来源类型的映射，如 {"#XXXXXX": "yearbook", "#YYYYYY": "report"}
 */
export async function runAnalysis(selectedColors = null, colorMapping = {}) {
  return request('/analyze', {
    method: 'POST',
    body: JSON.stringify({ 
      selected_colors: selectedColors,
      color_mapping: colorMapping,
    }),
  });
}

/**
 * 获取指标列表
 */
export async function getIndicators() {
  return request('/indicators');
}

/**
 * 获取某指标的匹配详情
 * @param {number} indicatorId
 */
export async function getMatches(indicatorId) {
  return request(`/indicator/${indicatorId}/matches`);
}

/**
 * 更新指标核对状态
 * @param {number} indicatorId
 * @param {string} status - "已确认" | "存疑"
 * @param {string} note - 备注
 */
export async function updateStatus(indicatorId, status, note = '') {
  return request(`/indicator/${indicatorId}/status`, {
    method: 'PUT',
    body: JSON.stringify({ status, note }),
  });
}

/**
 * 获取核对进度
 */
export async function getProgress() {
  return request('/progress');
}

/**
 * 导出报告（触发文件下载）
 */
export async function exportReport() {
  try {
    const res = await fetch(`${BASE}/export`, { method: 'GET' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '导出报告失败');
    }

    const disposition = res.headers.get('content-disposition');
    let filename = '报告数据核对结果.xlsx';
    if (disposition && disposition.includes('filename*=')) {
      filename = decodeURIComponent(disposition.split("UTF-8''")[1]);
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  } catch (error) {
    throw new Error(error.message || '导出报告失败，请检查网络');
  }
}

/**
 * 导出标色原表
 */
export async function exportOriginalReport() {
  try {
    const res = await fetch(`${BASE}/export_original`, { method: 'GET' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '导出标色原表失败');
    }

    const disposition = res.headers.get('content-disposition');
    let filename = '标色原表.xlsx';
    if (disposition && disposition.includes('filename*=')) {
      filename = decodeURIComponent(disposition.split("UTF-8''")[1]);
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  } catch (error) {
    throw new Error(error.message || '导出标色原表失败，请检查网络');
  }
}

/**
 * 导出 PDF 提取纯文本
 */
export async function exportText() {
  try {
    const res = await fetch(`${BASE}/export_text`, { method: 'GET' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '导出纯文本失败');
    }

    // 获取带编码的文件名
    const disposition = res.headers.get('content-disposition');
    let filename = 'PDF提取纯文本.txt';
    if (disposition && disposition.includes('filename*=')) {
      filename = decodeURIComponent(disposition.split("UTF-8''")[1]);
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  } catch (error) {
    throw new Error(error.message || '导出纯文本失败，请检查网络');
  }
}

/**
 * 获取 PDF 文件的 URL（供 react-pdf 加载）
 * @param {string} filename - PDF 文件名
 * @returns {string} URL
 */
export function getPdfUrl(filename) {
  return `${BASE}/pdf/${encodeURIComponent(filename)}`;
}

/**
 * 保存手动绑定映射
 * @param {number} indicatorId 
 * @param {string} pdfName 
 * @param {number} page 
 * @param {string} text 
 */
export async function saveManualBinding(indicatorId, pdfName, page, text) {
  return request('/manual_bind', {
    method: 'POST',
    body: JSON.stringify({ 
      indicator_id: indicatorId, 
      pdf_name: pdfName, 
      page, 
      selected_text: text 
    }),
  });
}
