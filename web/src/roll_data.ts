import * as z from "zod/mini";

export type RollDataConfig = {
    range: number;
    count: number;
    current_user_id: number;
    report_error_function: (msg: string, more_info?: Record<string, unknown>) => void;
    get_full_name: (user_id: number) => string;
};


export type Roll = {
    values: number[];
    total: number;
    user_id: number;
};

export type DisplayRoll = {
    values: number[];
    total: number;
    name: string;
};

export type WidgetData = {
    range: number;
    count: number;
    rolls: DisplayRoll[];
};

export const new_roll_schema = z.object({
    type: z.literal("new_roll"),
    values: z.array(z.number()),
    total: z.number(),
});
type NewRoll = {type: string, values: number[], total: number}

type NewRollRequest = {type: string}

export const roll_widget_extra_data_schema = z.object({
    range: z.number(), 
    count: z.number(),
});

export type RollWidgetExtraData = z.infer<typeof roll_widget_extra_data_schema>;

export type RollWidgetOutboundData = NewRollRequest

export class RollData {
    rolls: Roll[];
    range: number;
    count: number;
    current_user_id:number;
    roll_in_transit:boolean = false; //Flag for enabling and disabling the roll btn
    // Timer for roll ui button to re enable. Prevent users from spam clicking with a fallback 
    // just in case the roll doesnt reach the server.
    transit_timer: ReturnType<typeof setTimeout>|undefined = undefined; 
    report_error_function: (error_message: string) => void;
    get_full_name: (user_id: number) => string;
    constructor({
        range,
        count,
        current_user_id,
        report_error_function,
        get_full_name,
    }: RollDataConfig) {
        this.rolls = [];
        this.range = range;
        this.count = count;
        this.current_user_id = current_user_id;
        this.report_error_function = report_error_function;
        this.get_full_name = get_full_name;
    }

    // Creates a request to backend server to make a new roll
    new_roll_event(): NewRollRequest | undefined {
        // Prevent a new roll if a roll is in progress
        if(this.roll_in_transit) return undefined;
        const event = {
            type: "new_roll",
        };
        // Disable the roll btn
        this.roll_in_transit = true;
        return event;
    }

    // Roll inputs 
    handle_new_roll_event(sender_id: number, data: NewRoll): void {
        // Enable the roll btn if their request is received 
        if(this.roll_in_transit && sender_id === this.current_user_id){
            this.roll_in_transit = false;
        }
        const {values, total} = data;
        this.rolls.push({values, total, user_id:sender_id});
    }

    // Returns the important data from this object
    get_widget_data(): WidgetData{
        const display_rdy_rolls:DisplayRoll[] = [];
        for(const roll of this.rolls){
            display_rdy_rolls.push({
                values: roll.values,
                total: roll.total,
                name: this.get_full_name(roll.user_id),
            });

        }

        const widget_data = {
            range: this.range,
            count: this.count,
            rolls: display_rdy_rolls,
        };

        return widget_data;
    }
}