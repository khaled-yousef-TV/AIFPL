import React from 'react'
import { Clock, Loader2, CheckCircle2, AlertCircle } from 'lucide-react'
import type { Task } from '../types'

interface TasksTabProps {
  tasks: Task[]
}

const TasksTab: React.FC<TasksTabProps> = ({ tasks }) => {
  return (
    <div className="space-y-6">
      <div className="card">
        <div className="card-header">
          <div className="flex items-center gap-2">
            <Clock className="w-5 h-5 text-cyan-400" />
            <span>Background Tasks</span>
          </div>
        </div>
        <p className="text-gray-400 text-sm mb-4">
          Track the progress of background tasks like squad updates and Triple Captain calculations.
        </p>

        {tasks.length === 0 ? (
          <div className="text-center py-12 text-gray-400">
            <Clock className="w-12 h-12 mx-auto mb-4 opacity-50" />
            <p>No active tasks</p>
            <p className="text-xs mt-2">Tasks will appear here when you trigger background operations.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {tasks.map((task) => (
              <div
                key={task.id}
                className="bg-[#0f0f1a] rounded-lg border border-[#2a2a4a] p-4"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      {task.status === 'running' && (
                        <Loader2 className="w-4 h-4 text-cyan-400 animate-spin" />
                      )}
                      {task.status === 'completed' && (
                        <CheckCircle2 className="w-4 h-4 text-green-400" />
                      )}
                      {task.status === 'failed' && (
                        <AlertCircle className="w-4 h-4 text-red-400" />
                      )}
                      {task.status === 'pending' && (
                        <Clock className="w-4 h-4 text-gray-400" />
                      )}
                      <h3 className="font-medium text-white">{task.title}</h3>
                    </div>
                    <p className="text-sm text-gray-400">{task.description}</p>
                  </div>
                  <div className="text-right">
                    <div className={`text-xs font-medium px-2 py-1 rounded ${
                      task.status === 'running' ? 'bg-cyan-500/20 text-cyan-400' :
                      task.status === 'completed' ? 'bg-green-500/20 text-green-400' :
                      task.status === 'failed' ? 'bg-red-500/20 text-red-400' :
                      'bg-gray-500/20 text-gray-400'
                    }`}>
                      {task.status === 'running' ? 'Running' :
                       task.status === 'completed' ? 'Completed' :
                       task.status === 'failed' ? 'Failed' :
                       'Pending'}
                    </div>
                  </div>
                </div>

                {/* Progress Bar */}
                {(task.status === 'running' || task.status === 'pending') && (
                  <div className="mb-2">
                    <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
                      <span>Progress</span>
                      <span>{Math.round(task.progress)}%</span>
                    </div>
                    <div className="w-full bg-[#1a1a2e] rounded-full h-2 overflow-hidden">
                      <div
                        className="h-full bg-gradient-to-r from-cyan-500 to-cyan-400 transition-all duration-300 ease-out"
                        style={{ width: `${task.progress}%` }}
                      />
                    </div>
                  </div>
                )}

                {task.status === 'completed' && task.completedAt && (
                  <div className="text-xs text-gray-500 mt-2">
                    Completed {new Date(task.completedAt).toLocaleString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit'
                    })}
                  </div>
                )}

                {task.status === 'failed' && task.error && (
                  <div className="mt-2 p-2 bg-red-500/10 border border-red-500/30 rounded text-xs text-red-400">
                    {task.error}
                  </div>
                )}

                {task.status === 'completed' && (
                  <div className="mt-2 text-xs text-gray-500">
                    Started {new Date(task.createdAt).toLocaleString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit'
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default TasksTab

