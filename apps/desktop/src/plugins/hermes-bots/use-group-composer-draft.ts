import { host } from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'

import type { GroupChatRoom } from './group-chat'
import { groupComposerDraftKey, groupComposerDraftSnapshot, migrateGroupComposerDraft, updateGroupComposerDraft } from './group-panes'
import type { GroupComposerDraft, GroupDraftSetter } from './group-panes'
import { botsText } from './i18n'
import type { Attachment } from './types'

/** Draft state and cross-window annotation delivery for a legacy group room. */
export function useGroupComposerDraft(group: string, room: GroupChatRoom) {
  const composerKey = groupComposerDraftKey(group, room)
  const composerKeyRef = useRef(composerKey)
  const [composerDraft, setComposerDraft] = useState(() => groupComposerDraftSnapshot(composerKey))

  if (composerKeyRef.current !== composerKey) {
    migrateGroupComposerDraft(composerKeyRef.current, composerKey)
    composerKeyRef.current = composerKey
  }

  const updateComposerDraft = (mutate: (draft: GroupComposerDraft) => GroupComposerDraft) => {
    const next = updateGroupComposerDraft(composerKeyRef.current, mutate)
    setComposerDraft(next)

    return next
  }

  const draft = composerDraft.main || ''
  const replyDrafts = composerDraft.replies || {}
  const replyThread = composerDraft.activeReplyThread || null
  const pendingImages = composerDraft.pendingAttachments || {}

  const setDraft = (value: GroupDraftSetter<string>) =>
    updateComposerDraft(current => ({
      ...current,
      main: typeof value === 'function' ? value(current.main || '') : value
    }))

  const setReplyDrafts = (value: GroupDraftSetter<Record<string, string>>) =>
    updateComposerDraft(current => ({
      ...current,
      replies: typeof value === 'function' ? value(current.replies || {}) : value
    }))

  const setReplyThread = (value: GroupDraftSetter<null | string>) =>
    updateComposerDraft(current => ({
      ...current,
      activeReplyThread: typeof value === 'function' ? value(current.activeReplyThread || null) : value
    }))

  const setPendingImages = (value: GroupDraftSetter<Record<string, Attachment[]>>) =>
    updateComposerDraft(current => ({
      ...current,
      pendingAttachments: typeof value === 'function' ? value(current.pendingAttachments || {}) : value
    }))

  // Browser Comment Mode can live in its own Electron pop-out on another
  // monitor. The ordinary composer bus is window-local and this room uses its
  // own New Thread composer anyway, so accept only batches explicitly pinned to
  // THIS room + THIS renderer. Packaged Electron windows use the preload IPC
  // relay; BroadcastChannel stays only as a browser/dev fallback. The protocol
  // strings remain literal here to preserve the plugin fence (Bot Mode imports
  // only the SDK + its own files). A stale route gets no ACK, leaving the
  // annotations intact.
  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const desktopBridge = window.hermesDesktop?.windowRelay

    const channel =
      !desktopBridge && typeof BroadcastChannel !== 'undefined'
        ? new BroadcastChannel('hermes.desktop.preview-annotate-handoff.v1')
        : null

    if (!desktopBridge && !channel) {
      return
    }

    const send = (payload: unknown) => {
      if (desktopBridge) {
        desktopBridge.send(payload)
      } else {
        channel?.postMessage(payload)
      }
    }

    const onMessage = (data: unknown) => {
      if (!data || typeof data !== 'object') {
        return
      }

      const request = data as {
        count?: unknown
        destination?: { composerKey?: unknown; group?: unknown; kind?: unknown; windowId?: unknown }
        images?: unknown
        prompt?: unknown
        requestId?: unknown
        type?: unknown
      }

      const destination = request.destination
      const sourceWindowId = window.sessionStorage.getItem('hermes.desktop.previewAnnotate.windowId')?.trim()

      if (
        !sourceWindowId ||
        request.type !== 'preview-annotate-handoff' ||
        typeof request.requestId !== 'string' ||
        typeof request.prompt !== 'string' ||
        !Array.isArray(request.images) ||
        destination?.kind !== 'group' ||
        destination.windowId !== sourceWindowId ||
        destination.group !== group ||
        destination.composerKey !== composerKeyRef.current
      ) {
        return
      }

      try {
        const prompt = request.prompt

        const attachments: Attachment[] = request.images
          .filter((image): image is { dataUrl: string; name: string; number?: number } =>
            Boolean(
              image &&
              typeof image === 'object' &&
              typeof (image as { dataUrl?: unknown }).dataUrl === 'string' &&
              typeof (image as { name?: unknown }).name === 'string'
            )
          )
          .map(image => ({ data: image.dataUrl, kind: 'image' as const, name: image.name }))

        const next = updateGroupComposerDraft(composerKeyRef.current, current => ({
          ...current,
          activeReplyThread: null,
          main: current.main.trim() ? `${current.main.trimEnd()}\n\n${prompt}` : prompt,
          pendingAttachments: {
            ...(current.pendingAttachments || {}),
            main: [...(current.pendingAttachments?.main || []), ...attachments]
          }
        }))

        setComposerDraft(next)
        host.notify({
          kind: 'success',
          message: botsText().group.browserCommentsAdded(Number(request.count) || attachments.length, group)
        })
        send({
          ok: true,
          requestId: request.requestId,
          type: 'preview-annotate-handoff-ack'
        })
      } catch (error) {
        send({
          error: error instanceof Error ? error.message : String(error),
          ok: false,
          requestId: request.requestId,
          type: 'preview-annotate-handoff-ack'
        })
      }
    }

    const stopDesktop = desktopBridge?.onMessage(onMessage)
    const onBroadcast = (event: MessageEvent<unknown>) => onMessage(event.data)
    channel?.addEventListener('message', onBroadcast)

    return () => {
      stopDesktop?.()
      channel?.removeEventListener('message', onBroadcast)
      channel?.close()
    }
  }, [composerKey, group])


  return { composerKey, composerKeyRef, draft, replyDrafts, replyThread, pendingImages, setDraft, setReplyDrafts, setReplyThread, setPendingImages, setComposerDraft, updateComposerDraft }
}
