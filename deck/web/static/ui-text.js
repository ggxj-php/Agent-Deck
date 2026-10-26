/* 前端文案表。
 *
 * 聊天页 / 后台里状态栏、错误提示这类文案集中放这里，别散在 app.js / admin.js 里。
 * 想换语言或者改成别的口癖，只改这张表就行；运行时也能在控制台里
 *     UI_TEXT.copyInvite = 'Invite code copied'
 * 临时覆盖。
 *
 * 用法：t('deleteFailed', { code: 404 })
 */
window.UI_TEXT = {
  /* ---------- 通用 ---------- */
  unknownReason: '未知原因',
  loading: '正在想…',        // 发出消息后、首字还没回来的那段占位
  offline: '连不上服务',
  offlineRefresh: '连不上服务，刷新一下吧',
  reconnecting: '重连中…',
  restarting: '服务刚重启，等一秒再试…',
  copyInvite: '邀请码已复制',
  copyFailed: '复制失败，手动记一下：{code}',
  passwordSaved: '密码改好啦',

  /* ---------- 会话 ---------- */
  newSessionFailed: '开新会话失败：{reason}',
  createSessionFailed: '建会话失败：{reason}',
  deleteFailed: '删不掉（HTTP {code}）',
  switchFailed: '切不过去（HTTP {code}），刷新一下试试',
  switchFailedReason: '切不过去：{reason}',
  sessionGone: '这条会话已经不在了',
  busySwitch: '还在回话，等这一轮说完再切会话',

  /* ---------- 图片 ---------- */
  readImageFailed: '读图片失败',
  imagesSelected: '已选 {n} 张',
  imagesRejected: '有 {n} 张没加上（不是图片 / 超过 4MB / 最多 {max} 张）',

  /* ---------- 聊天里的提示条 ---------- */
  errorPrefix: '出错了：',
  continueBtn: '点我继续',
  cutNotice: '话说一半就断了（{reason}）。点下面按钮让它接着讲。',
  compressedNotice: '这段聊得有点长，前面 {n} 条旧记录已压成摘要（上下文 {before}k → {after}k），接着聊就好～',

  /* ---------- 后台 ---------- */
  adminSaved: '存好了',
  adminSaveFailed: '存不下来',
  adminLoadFailed: '拉不到数据',
};

/* 取一条文案：{{name}} 这类占位符会被替换 */
window.t = function t(key, params) {
  const table = window.UI_TEXT || {};
  let text = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : key;
  if (params) {
    Object.keys(params).forEach((name) => {
      text = String(text).split('{' + name + '}').join(params[name]);
    });
  }
  return text;
};
