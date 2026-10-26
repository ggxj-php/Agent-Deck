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
  offline: '连不上服务喵',
  offlineRefresh: '连不上服务喵，刷新一下吧',
  reconnecting: '重连中喵…',
  restarting: '服务刚重启，等一秒再试喵…',
  copyInvite: '邀请码已复制喵',
  copyFailed: '复制失败，手动记一下：{code}',
  passwordSaved: '密码改好啦喵',

  /* ---------- 会话 ---------- */
  newSessionFailed: '开新会话失败：{reason}',
  createSessionFailed: '建会话失败：{reason}',
  deleteFailed: '删不掉喵（HTTP {code}）',
  switchFailed: '切不过去喵（HTTP {code}），刷新一下试试',
  switchFailedReason: '切不过去喵：{reason}',
  sessionGone: '这条会话已经不在了喵',
  busySwitch: '还在回话喵，等这一轮说完再切会话',

  /* ---------- 图片 ---------- */
  readImageFailed: '读图片失败喵',
  imagesSelected: '已选 {n} 张喵',
  imagesRejected: '有 {n} 张没加上（不是图片 / 超过 4MB / 最多 {max} 张）',

  /* ---------- 后台 ---------- */
  adminSaved: '存好了喵',
  adminSaveFailed: '存不下来喵',
  adminLoadFailed: '拉不到数据喵',
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
