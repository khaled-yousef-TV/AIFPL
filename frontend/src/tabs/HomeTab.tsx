import { ChevronRight } from 'lucide-react'

interface NavigationTab {
  id: string
  label: string
  icon: React.ComponentType<{ className?: string }>
  description: string
  color: string
}

interface HomeTabProps {
  navigationTabs: NavigationTab[]
  setActiveTab: (tab: string) => void
}

export function HomeTab({ navigationTabs, setActiveTab }: HomeTabProps) {
  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-8">
        <h2 className="text-2xl font-bold mb-2">Welcome to FPL Squad Suggester</h2>
        <p className="text-gray-400">Choose a section to get started</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {navigationTabs.map(tab => {
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className="group relative p-6 bg-[#1a1a2e] rounded-lg border border-[#2a2a4a] hover:border-[#00ff87]/50 transition-all hover:shadow-lg hover:shadow-[#00ff87]/10 text-left"
            >
              <div className="flex items-start gap-4">
                <div className={`p-3 rounded-lg bg-[#0f0f1a] ${tab.color} group-hover:scale-110 transition-transform flex-shrink-0`}>
                  <tab.icon className="w-6 h-6" />
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-lg mb-1 group-hover:text-[#00ff87] transition-colors break-words leading-tight">
                    {tab.label}
                  </h3>
                  <p className="text-sm text-gray-400 break-words leading-relaxed">
                    {tab.description}
                  </p>
                </div>
                <ChevronRight className="w-5 h-5 text-gray-500 group-hover:text-[#00ff87] group-hover:translate-x-1 transition-all flex-shrink-0" />
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

