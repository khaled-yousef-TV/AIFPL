import { useState, useEffect, useCallback } from 'react'

import type { SavedFplTeam } from '../types'

// FPL team import cluster extracted from App.tsx.
// Setters/refs the moved code closed over are passed in as parameters
// (typed loosely on purpose - App.tsx used `any` in these flows).
export function useFplImport(params: {
  API_BASE: string
  setMySquad: (value: any) => void
  setBank: (value: any) => void
  setBankInput: (value: any) => void
  setWildcardPlan: (value: any) => void
  setTransferSuggestions: (value: any) => void
  setSquadAnalysis: (value: any) => void
  squadSectionRef: any
}) {
  const {
    API_BASE,
    setMySquad,
    setBank,
    setBankInput,
    setWildcardPlan,
    setTransferSuggestions,
    setSquadAnalysis,
    squadSectionRef,
  } = params

  // FPL team import
  const [fplTeamId, setFplTeamId] = useState<string>('')
  const [importingFplTeam, setImportingFplTeam] = useState(false)

  // Saved FPL team IDs (types imported from ../types)
  const [savedFplTeams, setSavedFplTeams] = useState<SavedFplTeam[]>([])
  const [selectedSavedFplTeamId, setSelectedSavedFplTeamId] = useState<number | ''>('')

  // Load saved FPL team IDs from database
  const loadSavedFplTeams = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/fpl-teams`)
      if (!res.ok) {
        console.error(`Failed to load saved FPL teams: HTTP ${res.status}`)
        setSavedFplTeams([])
        return
      }
      const data = await res.json()
      if (data.teams && Array.isArray(data.teams)) {
        // Map API response to frontend format
        const mapped: SavedFplTeam[] = data.teams.map((t: any) => ({
          teamId: t.teamId,
          teamName: t.teamName,
          lastImported: t.lastImported ? new Date(t.lastImported).getTime() : Date.now()
        }))
        setSavedFplTeams(mapped)
        console.log(`Loaded ${mapped.length} saved FPL team(s) from database`)
      } else {
        console.warn('Unexpected response format from fpl-teams endpoint:', data)
        setSavedFplTeams([])
      }
    } catch (err) {
      console.error('Failed to load saved FPL teams:', err)
      setSavedFplTeams([])
    }
  }, [])

  // Load FPL teams on mount
  useEffect(() => {
    loadSavedFplTeams()
  }, [loadSavedFplTeams])

  // Import squad from saved FPL team ID (always fetches latest from FPL)
  const importFromSavedFplTeam = useCallback(async (teamId: number) => {
    setImportingFplTeam(true)
    try {
      // Always fetch latest team data from FPL API
      const res = await fetch(`${API_BASE}/api/fpl-teams/import/${teamId}`)
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: 'Failed to import FPL team' }))
        throw new Error(error.detail || 'Failed to import FPL team')
      }

      const data = await res.json()
      const squad = data.squad || []
      const importedBank = data.bank || 0
      const teamName = data.team_name || `FPL Team ${teamId}`

      // Backend automatically saves/updates FPL team in database, so reload the list
      await loadSavedFplTeams()

      // Load the squad into the UI
      setMySquad(squad)
      setBank(importedBank)
      setBankInput(String(importedBank))

      // Reset view when importing a new squad
      setWildcardPlan(null)
      setTransferSuggestions([])
      setSquadAnalysis([])

      // Reset dropdown selection so it can be used again
      setSelectedSavedFplTeamId('')

      alert(`Successfully imported ${teamName}!`)

      // Scroll to squad section after import
      setTimeout(() => {
        if (squadSectionRef.current) {
          squadSectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      }, 100)
    } catch (err: any) {
      console.error('Failed to import FPL team:', err)
      alert(err.message || 'Failed to import FPL team. Please check the Team ID and try again.')
    } finally {
      setImportingFplTeam(false)
    }
  }, [loadSavedFplTeams])

  const importFplTeam = useCallback(async () => {
    const teamId = parseInt(fplTeamId.trim())
    if (!teamId || isNaN(teamId) || teamId <= 0) {
      alert('Please enter a valid FPL Team ID')
      return
    }

    setImportingFplTeam(true)
    try {
      // Import team from FPL (backend automatically saves to database)
      const res = await fetch(`${API_BASE}/api/fpl-teams/import/${teamId}`)
      if (!res.ok) {
        const error = await res.json().catch(() => ({ detail: 'Failed to import FPL team' }))
        throw new Error(error.detail || 'Failed to import FPL team')
      }

      const data = await res.json()
      const squad = data.squad || []
      const importedBank = data.bank || 0
      const teamName = data.team_name || `FPL Team ${teamId}`

      // Backend automatically saves/updates FPL team in database, so reload the list
      await loadSavedFplTeams()

      // Load the squad into the UI
      setMySquad(squad)
      setBank(importedBank)
      setBankInput(String(importedBank))

      // Reset view when importing a new squad
      setWildcardPlan(null)
      setTransferSuggestions([])
      setSquadAnalysis([])

      // Clear the input
      setFplTeamId('')
      setSelectedSavedFplTeamId('')

      alert(`Successfully imported ${teamName}!`)

      // Scroll to squad section after import
      setTimeout(() => {
        if (squadSectionRef.current) {
          squadSectionRef.current.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      }, 100)
    } catch (err: any) {
      console.error('Failed to import FPL team:', err)
      alert(err.message || 'Failed to import FPL team. Please check the Team ID and try again.')
    } finally {
      setImportingFplTeam(false)
    }
  }, [fplTeamId, loadSavedFplTeams])

  return {
    fplTeamId,
    setFplTeamId,
    importingFplTeam,
    savedFplTeams,
    selectedSavedFplTeamId,
    setSelectedSavedFplTeamId,
    loadSavedFplTeams,
    importFromSavedFplTeam,
    importFplTeam,
  }
}
