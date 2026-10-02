import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../api/client'

const POLL_INTERVAL_MS = 1200
const MAX_POLLS = 300 // ~6 minutes, matching the worker's soft time limit

/**
 * Submit background work and poll it to completion.
 *
 * This is the client half of the async contract: the POST returns a task id
 * immediately, and the UI polls `/api/tasks/{id}` so a Prophet fit or an
 * IsolationForest run never blocks a request.
 */
export function useTask({ onComplete } = {}) {
  const [task, setTask] = useState(null)
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const timerRef = useRef(null)
  const pollCountRef = useRef(0)
  const mountedRef = useRef(true)
  const onCompleteRef = useRef(onComplete)
  onCompleteRef.current = onComplete

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      stop()
    }
  }, [stop])

  const poll = useCallback(async (taskId) => {
    if (!mountedRef.current) return

    pollCountRef.current += 1
    if (pollCountRef.current > MAX_POLLS) {
      setError(new Error('Task is taking longer than expected; stopped polling.'))
      return
    }

    try {
      const record = await api.task(taskId)
      if (!mountedRef.current) return
      setTask(record)

      if (record.status === 'COMPLETED') {
        onCompleteRef.current?.(record)
        return
      }
      if (record.status === 'FAILED') {
        setError(new Error(record.error || 'The background task failed.'))
        return
      }
      timerRef.current = setTimeout(() => poll(taskId), POLL_INTERVAL_MS)
    } catch (err) {
      if (!mountedRef.current) return
      // A transient poll failure should not kill the run; retry a few times.
      if (pollCountRef.current < 5) {
        timerRef.current = setTimeout(() => poll(taskId), POLL_INTERVAL_MS * 2)
        return
      }
      setError(err)
    }
  }, [])

  const run = useCallback(async (submitFn) => {
    stop()
    pollCountRef.current = 0
    setError(null)
    setSubmitting(true)

    try {
      const submission = await submitFn()
      if (!mountedRef.current) return null
      setTask({ ...submission, progress: 0, stage: 'Queued' })
      poll(submission.task_id)
      return submission
    } catch (err) {
      if (mountedRef.current) setError(err)
      return null
    } finally {
      if (mountedRef.current) setSubmitting(false)
    }
  }, [poll, stop])

  const isRunning = Boolean(
    submitting || (task && (task.status === 'PENDING' || task.status === 'PROCESSING')),
  )

  return {
    task,
    error,
    isRunning,
    run,
    reset: useCallback(() => {
      stop()
      setTask(null)
      setError(null)
    }, [stop]),
  }
}
