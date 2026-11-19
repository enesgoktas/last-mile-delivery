-- OSRM Lua profile for HGV (Heavy Goods Vehicle / Truck) routing.
--
-- Derived from OSRM's built-in car.lua with the following changes:
--   • Primary access tag is "hgv", not "motorcar".
--   • Reads maxweight, maxheight, maxlength, maxwidth and rejects ways that
--     exceed the default HGV physical envelope.
--   • Rejects ways tagged hazmat=no.
--   • Rejects ways tagged hgv=no.
--   • Uses lower default speeds befitting a loaded truck.
--   • Tunnels: respects tunnel:maxweight / tunnel:maxheight.
--
-- Self-contained: the two OSRM lib helpers we need (Set and find_access_tag)
-- are inlined so this profile works when loaded from any path, not just /opt/.

api_version = 4

-- ---------------------------------------------------------------------------
-- Inline helpers
-- ---------------------------------------------------------------------------

local function Set(list)
    local s = {}
    for _, v in ipairs(list) do s[v] = true end
    return s
end

local function find_access_tag(source, tag_keys)
    for _, key in ipairs(tag_keys) do
        local v = source:get_value_by_key(key)
        if v then return v end   -- nil means tag absent; return first found value
    end
    return nil
end

-- ---------------------------------------------------------------------------
-- Limits for a default 12-tonne rigid HGV
-- ---------------------------------------------------------------------------

local MAX_WEIGHT_T = 12.0   -- tonnes
local MAX_HEIGHT_M =  4.0   -- metres
local MAX_LENGTH_M = 12.0   -- metres
local MAX_WIDTH_M  =  2.55  -- metres

-- ---------------------------------------------------------------------------
-- Lookup tables
-- ---------------------------------------------------------------------------

local speeds = {
    motorway        = 80,
    motorway_link   = 50,
    trunk           = 60,
    trunk_link      = 40,
    primary         = 55,
    primary_link    = 40,
    secondary       = 45,
    secondary_link  = 35,
    tertiary        = 40,
    tertiary_link   = 30,
    unclassified    = 25,
    residential     = 20,
    service         = 15,
    living_street   = 10,
    track           =  5,
    ferry           =  5,
    movable         =  5,
    shuttle_train   = 10,
}

local excluded_highway_types = Set {
    "cycleway", "footway", "path", "pedestrian", "steps",
    "proposed", "construction", "bridleway", "abandoned",
    "platform", "raceway", "razed",
}

local access_tag_whitelist = Set {
    "yes", "permissive", "designated", "destination", "delivery",
}

local access_tag_blacklist = Set {
    "no", "agricultural", "forestry", "private", "customers", "emergency",
}

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

local function parse_maxspeed(source)
    if not source then return 0 end
    local n = tonumber(source)
    if n then return n end
    local mph = string.match(source, "(%d+)%s*mph")
    if mph then return math.floor(tonumber(mph) * 1.609344) end
    return 0
end

local function parse_limit(value)
    if not value then return nil end
    return tonumber(string.match(value, "[%d%.]+"))
end

local function exceeds_physical_envelope(way)
    local w = parse_limit(way:get_value_by_key("maxweight"))
    if w and w < MAX_WEIGHT_T then return true end

    local tw = parse_limit(way:get_value_by_key("tunnel:maxweight"))
    if tw and tw < MAX_WEIGHT_T then return true end

    local h = parse_limit(way:get_value_by_key("maxheight"))
    if h and h < MAX_HEIGHT_M then return true end

    local th = parse_limit(way:get_value_by_key("tunnel:maxheight"))
    if th and th < MAX_HEIGHT_M then return true end

    local l = parse_limit(way:get_value_by_key("maxlength"))
    if l and l < MAX_LENGTH_M then return true end

    local ww = parse_limit(way:get_value_by_key("maxwidth"))
    if ww and ww < MAX_WIDTH_M then return true end

    return false
end

-- ---------------------------------------------------------------------------
-- OSRM API version 4 callbacks (must be global functions)
-- ---------------------------------------------------------------------------

function setup()
    return {
        properties = {
            max_speed_for_map_matching      = 180 / 3.6,
            weight_name                     = "duration",
            process_call_tagless_node       = false,
            u_turn_penalty                  = 20,
            continue_straight_at_waypoint   = true,
            use_turn_restrictions           = true,
            left_hand_driving               = false,
            traffic_light_penalty           = 2,
        },
        default_mode    = mode.driving,
        default_speed   = 10,
        oneway_handling = true,
        turn_penalty    = 7.5,
        turn_bias       = 1.075,
    }
end

function process_node(profile, node, result, relations)
    local access = find_access_tag(node, {"hgv", "vehicle", "motor_vehicle", "access"})
    if access and access_tag_blacklist[access] then
        result.barrier = true
        return
    end
    local barrier = node:get_value_by_key("barrier")
    if barrier then
        local bollard = node:get_value_by_key("bollard")
        local access2 = node:get_value_by_key("access")
        if bollard ~= "removable" and bollard ~= "rising" and
           access2 ~= "yes" and access2 ~= "permissive" then
            result.barrier = true
        end
    end
end

function process_way(profile, way, result, relations)
    local highway = way:get_value_by_key("highway")
    local route   = way:get_value_by_key("route")
    local bridge  = way:get_value_by_key("bridge")

    -- Only process routable highways; allow ferries.
    if not highway or highway == "" then
        if route == "ferry" then
            result.forward_mode   = mode.ferry
            result.backward_mode  = mode.ferry
            result.forward_speed  = speeds["ferry"]
            result.backward_speed = speeds["ferry"]
        end
        return
    end

    -- Hard exclusions.
    if excluded_highway_types[highway] then return end

    -- Access: hgv > vehicle > motor_vehicle > access
    local access = find_access_tag(way, {"hgv", "vehicle", "motor_vehicle", "access"})
    if access and access_tag_blacklist[access] then return end

    -- Physical limits.
    if exceeds_physical_envelope(way) then return end

    -- Hazardous materials ban (only reject when explicitly "no").
    local hazmat = way:get_value_by_key("hazmat")
    if hazmat == "no" then return end

    -- Resolve speed for this highway class.
    local speed = speeds[highway]
    if not speed then
        -- Unknown highway type: allow only if access is explicitly whitelisted.
        if access and access_tag_whitelist[access] then
            speed = 10
        else
            return
        end
    end

    -- Honour posted speed limit (trucks capped at 90 km/h).
    local maxspeed = parse_maxspeed(way:get_value_by_key("maxspeed"))
    if maxspeed > 0 then speed = math.min(speed, maxspeed) end
    speed = math.min(speed, 90)

    -- Directionality.
    local oneway = way:get_value_by_key("oneway")
    if oneway == "yes" or oneway == "1" or oneway == "true" then
        result.forward_mode   = mode.driving
        result.backward_mode  = mode.inaccessible
        result.forward_speed  = speed
    elseif oneway == "-1" then
        result.forward_mode   = mode.inaccessible
        result.backward_mode  = mode.driving
        result.backward_speed = speed
    else
        result.forward_mode   = mode.driving
        result.backward_mode  = mode.driving
        result.forward_speed  = speed
        result.backward_speed = speed
    end

    -- Movable bridge.
    if bridge == "movable" then
        result.forward_speed  = speeds["movable"]
        result.backward_speed = speeds["movable"]
        result.forward_mode   = mode.driving
        result.backward_mode  = mode.driving
    end

    result.forward_rate  = result.forward_speed  / 3.6
    result.backward_rate = result.backward_speed / 3.6
end

function process_turn(profile, turn)
    turn.duration = 0.0
    if turn.is_u_turn then
        turn.duration = profile.properties.u_turn_penalty
    end
end

return {
    setup        = setup,
    process_way  = process_way,
    process_node = process_node,
    process_turn = process_turn,
}
