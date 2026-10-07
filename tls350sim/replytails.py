# Tank Monitor Console Simulator -- a training simulator for TLS-350
# compatible tank monitor consoles.
# Copyright (C) 2026 Verbose Software
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License (LICENSE) for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
"""How many blank lines the bench TLS-350 sends before ETX, code by code.

FIDELITY S6's tail. The count is a property of the code and not of what the
report holds: every one of the 485 display codes the bench answers ended the
same way in four captures of two different states -- out of a cold start
(`cap_coldstart`) and after the Set sweep in U.S., metric and imperial
units (`cap_swept`, `cap_metric`, `cap_imperial`), 2026-09-18 and 19. Nothing
on a page predicts it, so it is recorded. Generated from `cap_swept` by the
pass that measured it; `Handler.inquire` applies it.
"""

#: {blank lines before ETX: the codes that end with that many}
BY_COUNT = {
    0: frozenset({
        "116", "520", "523", "524", "525", "526", "527", "528", "52A", "52B",
        "52E", "535", "5FA", "602", "603", "604", "607", "608", "609", "60C",
        "60F", "610", "612", "61A", "61D", "621", "622", "623", "625", "626",
        "628", "629", "62A", "634", "635", "636", "63C", "7B3", "7C3", "901",
        "903",
    }),
    1: frozenset({
        "101", "102", "111", "112", "114", "201", "202", "203", "204", "207",
        "208", "209", "20A", "20B", "20C", "20D", "20E", "20F", "212", "214",
        "215", "216", "217", "218", "219", "21B", "251", "281", "282", "301",
        "302", "306", "307", "311", "312", "315", "316", "317", "318", "319",
        "31A", "331", "332", "333", "341", "342", "346", "347", "34B", "34C",
        "351", "352", "353", "373", "374", "375", "381", "382", "383", "384",
        "385", "386", "387", "388", "389", "38A", "391", "392", "401", "402",
        "403", "406", "502", "503", "505", "506", "507", "508", "509", "50A",
        "50B", "50C", "50D", "50E", "50F", "510", "511", "512", "513", "514",
        "515", "516", "517", "518", "519", "51A", "51B", "51C", "51D", "51E",
        "51F", "521", "522", "529", "52D", "52F", "530", "531", "532", "533",
        "534", "536", "537", "538", "539", "53A", "53B", "53C", "53D", "53E",
        "53F", "540", "541", "542", "543", "546", "547", "548", "549", "54A",
        "54B", "54C", "553", "554", "555", "556", "557", "558", "559", "55A",
        "55B", "55D", "560", "5BD", "5BE", "5BF", "5E2", "601", "605", "606",
        "60A", "60B", "60E", "611", "615", "616", "619", "61E", "624", "627",
        "62B", "62C", "62E", "62F", "630", "631", "632", "633", "63A", "681",
        "682", "683", "701", "702", "703", "704", "706", "707", "708", "709",
        "711", "712", "713", "721", "722", "723", "724", "725", "726", "727",
        "728", "729", "72A", "72B", "72C", "731", "732", "733", "734", "741",
        "742", "743", "744", "746", "747", "748", "749", "74B", "74C", "74D",
        "74E", "751", "752", "753", "754", "755", "756", "757", "758", "759",
        "75B", "75C", "75D", "75E", "75F", "760", "761", "762", "771", "772",
        "773", "775", "776", "777", "778", "779", "77A", "77B", "77C", "77D",
        "77E", "77F", "780", "781", "782", "783", "784", "785", "786", "788",
        "789", "78A", "78C", "78D", "78E", "78F", "790", "791", "792", "793",
        "794", "795", "796", "797", "798", "799", "79A", "79D", "79F", "7A0",
        "7A1", "7A2", "7A3", "7A4", "7A5", "7A6", "7A7", "7A8", "7A9", "7AC",
        "7AD", "7AF", "7B2", "7BC", "7BE", "7C1", "7C2", "7D0", "7D2", "7D3",
        "7D4", "801", "802", "803", "804", "806", "807", "808", "809", "80A",
        "80B", "80C", "851", "852", "853", "881", "882", "885", "886", "887",
        "889", "88D", "891", "892", "8BC", "902", "905", "A01", "A02", "A03",
        "A04", "A05", "A06", "A07", "A10", "A11", "A12", "A13", "A14", "A15",
        "A20", "A21", "A22", "A23", "A51", "A52", "A53", "A54", "A55", "A61",
        "A62", "A63", "A81", "A91", "B01", "B06", "B07", "B11", "B21", "B31",
        "B33", "B34", "B35", "B36", "B37", "B38", "B39", "B41", "B46", "B4B",
        "B50", "B51", "B52", "B53", "B54", "B71", "B7B", "B7C", "B7D", "B7E",
        "B7F", "B81", "B82", "B83", "B87", "B88", "B89", "B8A", "B8B", "B8C",
        "B8D", "B8E", "B91", "B92", "B93", "B94", "C05", "C06", "V00", "V01",
        "V10", "V40", "V41", "V42", "V44", "V45", "V46", "V47", "V48", "V49",
        "V4A", "V4B", "V4C", "V4D", "V4E", "V4F", "V50", "V51", "V52", "V80",
        "V81", "V85", "VC0", "VC1", "VC3", "VC4", "VC5",
    }),
    2: frozenset({
        "113", "121", "205", "206", "501", "52C", "5BC", "613", "614", "617",
        "618", "61B", "61C", "62D", "63B", "680", "75A", "787", "79E", "7B0",
        "7BD", "888",
    }),
    3: frozenset({
        "504", "639", "7B1",
    }),
    4: frozenset({
        "904", "BA0",
    }),
}

#: {code: blank lines before ETX}
TAIL = {code: n for n, codes in BY_COUNT.items() for code in codes}

#: {code: blank lines before ETX when the reply has a BODY}, where it is not
#: TAIL's. TAIL was measured with every tank and line switched off, and these
#: codes answered bare then; with Q1 on (`cap_q1on`, `cap_site`) and again
#: with tank 1 and Q1 on (2026-09-24, devices 00 and 01) each closed with the
#: count below, the same in every capture.
BODY_TAIL = {"373": 4, "374": 0, "375": 4, "381": 2, "382": 2, "383": 3,
             "384": 0, "385": 4, "780": 2, "B7B": 0, "B7C": 0, "B7E": 3}
