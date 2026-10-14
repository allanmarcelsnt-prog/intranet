export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18"
  }
  public: {
    Tables: {
      bypasses: {
        Row: {
          archived: boolean
          archived_at: string | null
          client_id: string
          created_at: string
          end_date: string | null
          id: string
          operator_id: string | null
          reason: string | null
          sensor_id: string | null
          sensor_label: string | null
          start_date: string
        }
        Insert: {
          archived?: boolean
          archived_at?: string | null
          client_id: string
          created_at?: string
          end_date?: string | null
          id?: string
          operator_id?: string | null
          reason?: string | null
          sensor_id?: string | null
          sensor_label?: string | null
          start_date?: string
        }
        Update: {
          archived?: boolean
          archived_at?: string | null
          client_id?: string
          created_at?: string
          end_date?: string | null
          id?: string
          operator_id?: string | null
          reason?: string | null
          sensor_id?: string | null
          sensor_label?: string | null
          start_date?: string
        }
        Relationships: [
          {
            foreignKeyName: "bypasses_client_id_fkey"
            columns: ["client_id"]
            isOneToOne: false
            referencedRelation: "clients"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "bypasses_sensor_id_fkey"
            columns: ["sensor_id"]
            isOneToOne: false
            referencedRelation: "client_sensors"
            referencedColumns: ["id"]
          },
        ]
      }
      client_devices: {
        Row: {
          active: boolean
          client_id: string
          created_at: string
          id: string
          name: string
          type: string
        }
        Insert: {
          active?: boolean
          client_id: string
          created_at?: string
          id?: string
          name: string
          type?: string
        }
        Update: {
          active?: boolean
          client_id?: string
          created_at?: string
          id?: string
          name?: string
          type?: string
        }
        Relationships: [
          {
            foreignKeyName: "client_devices_client_id_fkey"
            columns: ["client_id"]
            isOneToOne: false
            referencedRelation: "clients"
            referencedColumns: ["id"]
          },
        ]
      }
      client_events: {
        Row: {
          archived: boolean
          archived_at: string | null
          by_operator: boolean
          client_id: string
          created_at: string
          custom: Json
          description: string | null
          device_name: string | null
          end_time: string | null
          event_date: string
          event_time: string | null
          id: string
          kind: Database["public"]["Enums"]["event_kind"]
          operator_id: string | null
          sensor_id: string | null
          sensor_ids: string[]
          sensor_labels: string | null
          status: string | null
          user_name: string | null
        }
        Insert: {
          archived?: boolean
          archived_at?: string | null
          by_operator?: boolean
          client_id: string
          created_at?: string
          custom?: Json
          description?: string | null
          device_name?: string | null
          end_time?: string | null
          event_date?: string
          event_time?: string | null
          id?: string
          kind: Database["public"]["Enums"]["event_kind"]
          operator_id?: string | null
          sensor_id?: string | null
          sensor_ids?: string[]
          sensor_labels?: string | null
          status?: string | null
          user_name?: string | null
        }
        Update: {
          archived?: boolean
          archived_at?: string | null
          by_operator?: boolean
          client_id?: string
          created_at?: string
          custom?: Json
          description?: string | null
          device_name?: string | null
          end_time?: string | null
          event_date?: string
          event_time?: string | null
          id?: string
          kind?: Database["public"]["Enums"]["event_kind"]
          operator_id?: string | null
          sensor_id?: string | null
          sensor_ids?: string[]
          sensor_labels?: string | null
          status?: string | null
          user_name?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "client_events_client_id_fkey"
            columns: ["client_id"]
            isOneToOne: false
            referencedRelation: "clients"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "client_events_sensor_id_fkey"
            columns: ["sensor_id"]
            isOneToOne: false
            referencedRelation: "client_sensors"
            referencedColumns: ["id"]
          },
        ]
      }
      client_sensors: {
        Row: {
          active: boolean
          client_id: string
          created_at: string
          id: string
          name: string
          type: string
          zone: string
        }
        Insert: {
          active?: boolean
          client_id: string
          created_at?: string
          id?: string
          name: string
          type?: string
          zone: string
        }
        Update: {
          active?: boolean
          client_id?: string
          created_at?: string
          id?: string
          name?: string
          type?: string
          zone?: string
        }
        Relationships: [
          {
            foreignKeyName: "client_sensors_client_id_fkey"
            columns: ["client_id"]
            isOneToOne: false
            referencedRelation: "clients"
            referencedColumns: ["id"]
          },
        ]
      }
      client_users: {
        Row: {
          active: boolean
          client_id: string
          created_at: string
          id: string
          name: string
          role: string | null
        }
        Insert: {
          active?: boolean
          client_id: string
          created_at?: string
          id?: string
          name: string
          role?: string | null
        }
        Update: {
          active?: boolean
          client_id?: string
          created_at?: string
          id?: string
          name?: string
          role?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "client_users_client_id_fkey"
            columns: ["client_id"]
            isOneToOne: false
            referencedRelation: "clients"
            referencedColumns: ["id"]
          },
        ]
      }
      clients: {
        Row: {
          active: boolean
          address: string | null
          created_at: string
          id: string
          name: string
          notes: string | null
        }
        Insert: {
          active?: boolean
          address?: string | null
          created_at?: string
          id?: string
          name: string
          notes?: string | null
        }
        Update: {
          active?: boolean
          address?: string | null
          created_at?: string
          id?: string
          name?: string
          notes?: string | null
        }
        Relationships: []
      }
      custom_fields: {
        Row: {
          active: boolean
          created_at: string
          field_type: string
          id: string
          label: string
          options: string[]
          position: number
          scope: string
        }
        Insert: {
          active?: boolean
          created_at?: string
          field_type?: string
          id?: string
          label: string
          options?: string[]
          position?: number
          scope: string
        }
        Update: {
          active?: boolean
          created_at?: string
          field_type?: string
          id?: string
          label?: string
          options?: string[]
          position?: number
          scope?: string
        }
        Relationships: []
      }
      operator_access: {
        Row: {
          status: string
          updated_at: string
          user_id: string
        }
        Insert: {
          status?: string
          updated_at?: string
          user_id: string
        }
        Update: {
          status?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      profiles: {
        Row: {
          created_at: string
          email: string | null
          id: string
          name: string
        }
        Insert: {
          created_at?: string
          email?: string | null
          id: string
          name?: string
        }
        Update: {
          created_at?: string
          email?: string | null
          id?: string
          name?: string
        }
        Relationships: []
      }
      shifts: {
        Row: {
          ended_at: string | null
          id: string
          message: string | null
          next_operator_id: string | null
          operator_id: string
          shift_type: string
          started_at: string
        }
        Insert: {
          ended_at?: string | null
          id?: string
          message?: string | null
          next_operator_id?: string | null
          operator_id: string
          shift_type?: string
          started_at?: string
        }
        Update: {
          ended_at?: string | null
          id?: string
          message?: string | null
          next_operator_id?: string | null
          operator_id?: string
          shift_type?: string
          started_at?: string
        }
        Relationships: []
      }
      user_roles: {
        Row: {
          id: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Insert: {
          id?: string
          role: Database["public"]["Enums"]["app_role"]
          user_id: string
        }
        Update: {
          id?: string
          role?: Database["public"]["Enums"]["app_role"]
          user_id?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      archive_month: {
        Args: { _archived?: boolean; _month: string }
        Returns: number
      }
      can_manage: { Args: { _uid: string }; Returns: boolean }
      confirm_saved_archive: { Args: { _snapshot: Json }; Returns: number }
      has_role: {
        Args: {
          _role: Database["public"]["Enums"]["app_role"]
          _user_id: string
        }
        Returns: boolean
      }
      is_approved: { Args: { _uid: string }; Returns: boolean }
      prepare_archive_month: { Args: { _month: string }; Returns: Json }
      purge_expired_archives: { Args: never; Returns: number }
      set_operator_access: {
        Args: { _status: string; _user_id: string }
        Returns: undefined
      }
    }
    Enums: {
      app_role: "admin" | "operator" | "manager"
      event_kind: "arm" | "disarm" | "trigger" | "maintenance" | "observation"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {
      app_role: ["admin", "operator", "manager"],
      event_kind: ["arm", "disarm", "trigger", "maintenance", "observation"],
    },
  },
} as const
