/**
 * What a click on a notification does, shared by the bell and the pop-ups: the sender is told (the freedesktop `default` action, when it
 * offered one), the page it points to is opened (`link`, a path of this web UI), and it counts as read. A notification with neither
 * simply has nothing to open: the click only acknowledges it.
 */
import {api} from '@/api'

export function activate(n, router, close = () => {}) {
  if (n.default_action) api.Notifications.invoke({id: n.id, action: 'default'})
  api.Notifications.mark_read({ids: [n.id]})
  // The server only accepts a path of this web UI, and so does this: an address is never followed
  if (typeof n.link === 'string' && n.link.startsWith('/') && !n.link.startsWith('//')) router.push(n.link)
  close()
}

export function runAction(n, action) {
  api.Notifications.invoke({id: n.id, action: action.id})
  api.Notifications.mark_read({ids: [n.id]})
}
