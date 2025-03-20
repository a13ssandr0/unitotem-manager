descriptions = {
    400: 'The 400 (Bad Request) status code indicates that the server cannot or '
         'will not process the request due to something that is perceived to be a '
         'client error (e.g., malformed request syntax, invalid request message '
         'framing, or deceptive request routing).',
    401: 'The 401 (Unauthorized) status code indicates that the request has not '
         'been applied because it lacks valid authentication credentials for the '
         'target resource. The server generating a 401 response MUST send a '
         'WWW-Authenticate header field (Section 4.1) containing at least one '
         'challenge applicable to the target resource.',
    402: 'The 402 (Payment Required) status code is reserved for future use.',
    403: 'You are not authorized to access this page or complete this action. '
         'Please contact the administrator if you think you should be authorized.',
    404: 'The 404 (Not Found) status code indicates that the origin server did '
         'not find a current representation for the target resource or is not '
         'willing to disclose that one exists. A 404 status code does not '
         'indicate whether this lack of representation is temporary or permanent; '
         'the 410 (Gone) status code is preferred over 404 if the origin server '
         'knows, presumably through some configurable means, that the condition '
         'is likely to be permanent.',
    405: 'The 405 (Method Not Allowed) status code indicates that the method '
         'received in the request-line is known by the origin server but not '
         'supported by the target resource. The origin server MUST generate an '
         'Allow header field in a 405 response containing a list of the target '
         "resource's currently supported methods.",
    406: 'The 406 (Not Acceptable) status code indicates that the target resource '
         'does not have a current representation that would be acceptable to the '
         'user agent, according to the proactive negotiation header fields '
         'received in the request, and the server is unwilling to supply a '
         'default representation.',
    407: 'The 407 (Proxy Authentication Required) status code is similar to 401 '
         '(Unauthorized), but it indicates that the client needs to authenticate '
         'itself in order to use a proxy. The proxy MUST send a '
         'Proxy-Authenticate header field containing a challenge applicable to '
         'that proxy for the target resource. The client MAY repeat the request '
         'with a new or replaced Proxy-Authorization header field.',
    408: 'The 408 (Request Timeout) status code indicates that the server did not '
         'receive a complete request message within the time that it was prepared '
         'to wait. A server SHOULD send the "close" connection option in the '
         'response, since 408 implies that the server has decided to close the '
         'connection rather than continue waiting. If the client has an '
         'outstanding request in transit, the client MAY repeat that request on a '
         'new connection.',
    409: 'The 409 (Conflict) status code indicates that the request could not be '
         'completed due to a conflict with the current state of the target '
         'resource. This code is used in situations where the user might be able '
         'to resolve the conflict and resubmit the request. The server SHOULD '
         'generate a payload that includes enough information for a user to '
         'recognize the source of the conflict.',
    410: 'The 410 (Gone) status code indicates that access to the target resource '
         'is no longer available at the origin server and that this condition is '
         'likely to be permanent. If the origin server does not know, or has no '
         'facility to determine, whether or not the condition is permanent, the '
         'status code 404 (Not Found) ought to be used instead.',
    411: 'The 411 (Length Required) status code indicates that the server refuses '
         'to accept the request without a defined Content-Length. The client MAY '
         'repeat the request if it adds a valid Content-Length header field '
         'containing the length of the message body in the request message.',
    412: 'The 412 (Precondition Failed) status code indicates that one or more '
         'conditions given in the request header fields evaluated to false when '
         'tested on the server. This response code allows the client to place '
         'preconditions on the current resource state (its current '
         'representations and metadata) and, thus, prevent the request method '
         'from being applied if the target resource is in an unexpected state.',
    413: 'The 413 (Payload Too Large) status code indicates that the server is '
         'refusing to process a request because the request payload is larger '
         'than the server is willing or able to process. The server MAY close the '
         'connection to prevent the client from continuing the request.',
    414: 'The 414 (URI Too Long) status code indicates that the server is '
         'refusing to service the request because the request-target is longer '
         'than the server is willing to interpret.',
    415: 'The 415 (Unsupported Media Type) status code indicates that the origin '
         'server is refusing to service the request because the payload is in a '
         'format not supported by this method on the target resource. The format '
         "problem might be due to the request's indicated Content-Type or "
         'Content-Encoding, or as a result of inspecting the data directly.',
    416: 'The 416 (Range Not Satisfiable) status code indicates that none of the '
         "ranges in the request's Range header field (Section 3.1) overlap the "
         'current extent of the selected resource or that the set of ranges '
         'requested has been rejected due to invalid ranges or an excessive '
         'request of small or overlapping ranges.',
    417: 'The 417 (Expectation Failed) status code indicates that the expectation '
         "given in the request's Expect header field could not be met by at least "
         'one of the inbound servers.',
    418: "I cannot brew your coffee because I'm a teapot.",
    421: 'The 421 (Misdirected Request) status code indicates that the request '
         'was directed at a server that is not able to produce a response. This '
         'can be sent by a server that is not configured to produce responses for '
         'the combination of scheme and authority that are included in the '
         'request URI.',
    422: 'The 422 (Unprocessable Entity) status code means the server understands '
         'the content type of the request entity (hence a 415 (Unsupported Media '
         'Type) status code is inappropriate), and the syntax of the request '
         'entity is correct (thus a 400 (Bad Request) status code is '
         'inappropriate) but was unable to process the contained instructions. '
         'For example, this error condition may occur if an XML request body '
         'contains well-formed (i.e., syntactically correct), but semantically '
         'erroneous, XML instructions.',
    423: 'The 423 (Locked) status code means the source or destination resource '
         'of a method is locked. This response SHOULD contain an appropriate '
         "precondition or postcondition code, such as 'lock-token-submitted' or "
         "'no-conflicting-lock'.",
    424: 'The 424 (Failed Dependency) status code means that the method could not '
         'be performed on the resource because the requested action depended on '
         'another action and that action failed. For example, if a command in a '
         'PROPPATCH method fails, then, at minimum, the rest of the commands will '
         'also fail with 424 (Failed Dependency).',
    425: 'A 425 (Too Early) status code indicates that the server is unwilling to '
         'risk processing a request that might be replayed. User agents that send '
         'a request in early data are expected to retry the request when '
         'receiving a 425 (Too Early) response status code. A user agent SHOULD '
         'retry automatically, but any retries MUST NOT be sent in early data.',
    426: 'The 426 (Upgrade Required) status code indicates that the server '
         'refuses to perform the request using the current protocol but might be '
         'willing to do so after the client upgrades to a different protocol. The '
         'server MUST send an Upgrade header field in a 426 response to indicate '
         'the required protocol(s).',
    428: 'The 428 status code indicates that the origin server requires the '
         'request to be conditional.',
    429: 'The 429 status code indicates that the user has sent too many requests '
         'in a given amount of time ("rate limiting").',
    431: 'The 431 status code indicates that the server is unwilling to process '
         'the request because its header fields are too large. The request MAY be '
         'resubmitted after reducing the size of the request header fields.',
    451: 'This status code indicates that the server is denying access to the '
         'resource as a consequence of a legal demand. The server in question '
         'might not be an origin server. This type of legal demand typically most '
         'directly affects the operations of ISPs and search engines.',
    500: 'The 500 (Internal Server Error) status code indicates that the server '
         'encountered an unexpected condition that prevented it from fulfilling '
         'the request.',
    501: 'The 501 (Not Implemented) status code indicates that the server does '
         'not support the functionality required to fulfill the request. This is '
         'the appropriate response when the server does not recognize the request '
         'method and is not capable of supporting it for any resource.',
    502: 'The 502 (Bad Gateway) status code indicates that the server, while '
         'acting as a gateway or proxy, received an invalid response from an '
         'inbound server it accessed while attempting to fulfill the request.',
    503: 'The 503 (Service Unavailable) status code indicates that the server is '
         'currently unable to handle the request due to a temporary overload or '
         'scheduled maintenance, which will likely be alleviated after some '
         'delay. The server MAY send a Retry-After header field to suggest an '
         'appropriate amount of time for the client to wait before retrying the '
         'request.',
    504: 'The 504 (Gateway Timeout) status code indicates that the server, while '
         'acting as a gateway or proxy, did not receive a timely response from an '
         'upstream server it needed to access in order to complete the request.',
    505: 'The 505 (HTTP Version Not Supported) status code indicates that the '
         'server does not support, or refuses to support, the major version of '
         'HTTP that was used in the request message. The server is indicating '
         'that it is unable or unwilling to complete the request using the same '
         'major version as the client, other than with this error message. The '
         'server SHOULD generate a representation for the 505 response that '
         'describes why that version is not supported and what other protocols '
         'are supported by that server.',
    506: 'The 506 status code indicates that the server has an internal '
         'configuration error: the chosen variant resource is configured to '
         'engage in transparent content negotiation itself, and is therefore not '
         'a proper end point in the negotiation process.',
    507: 'The 507 (Insufficient Storage) status code means the method could not '
         'be performed on the resource because the server is unable to store the '
         'representation needed to successfully complete the request. This '
         'condition is considered to be temporary. If the request that received '
         'this status code was the result of a user action, the request MUST NOT '
         'be repeated until it is requested by a separate user action.',
    508: 'The 508 (Loop Detected) status code indicates that the server '
         'terminated an operation because it encountered an infinite loop while '
         'processing a request with "Depth: infinity". This status indicates that '
         'the entire operation failed.',
    510: 'The policy for accessing the resource has not been met in the request. '
         'The server should send back all the information necessary for the '
         'client to issue an extended request. It is outside the scope of this '
         'specification to specify how the extensions inform the client.',
    511: 'The 511 status code indicates that the client needs to authenticate to '
         'gain network access.'
}
